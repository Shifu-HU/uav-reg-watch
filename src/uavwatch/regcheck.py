"""法规条目的结构化要点提取。

现状问题（实测 181 条本地法规）：
    effective_at 只有 16% 有值 —— 84% 的法规飞手不知道什么时候生效
    ai_summary 偏笼统 —— "规范无人机飞行行为并保障安全" 这种等于没说
    category 是纯关键词匹配 —— "飞行审批"里装的全是适航标准

这里用本地小模型做**结构化抽取**，输出固定字段：
    action      这条要求我做什么（一句话，动词开头）
    who         对谁有效（个人飞手 / 企业 / 厂商 / 通用）
    penalty     违规罚则（没有则空）
    effective   生效日期
    keywords    3~5 个检索关键词

和 llmcheck 的区别：那边针对**禁飞通告**（时间/范围/对象），
这边针对**法规条文**（要求/罚则/生效）。两者字段完全不同，
所以分成两个模块而不是塞进一个提示词。
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("uavwatch.regcheck")


# 注意：这里**不能**给 action 写示例句。
# 实测 0.8b 会把示例当答案照抄 —— 给它"写'需在飞行前完成实名登记'"
# 这条要求后，三条完全不同的法规都输出同一句话。
# 只描述字段含义，让它自己从原文读。
PROMPT = """读下面的法规，提取要点。只输出 JSON，不要解释，不要照抄说明。

{{"action": "这条法规要求飞手做什么，用你自己的话概括，20字以内", \
"who": "这条法规管的是谁，只填 个人飞手 或 企业 或 厂商 或 通用 之一", \
"penalty": "法规原文写明的违规罚则，没写就填空字符串", \
"effective": "法规原文写明的施行日期 YYYY-MM-DD，没写就填空字符串", \
"keywords": ["3到5个检索关键词"]}}

法规标题：{title}
法规内容：{body}
"""


class RegChecker:
    """提取法规要点。"""

    def __init__(self, llm):
        self.llm = llm
        # 记录出现过的 action，用来发现小模型"所有条目输出同一句"的
        # 失效模式。真出现时把重复的丢掉，宁可没有要点也不要错的。
        self._seen: dict[str, int] = {}

    def _repeated(self, action: str) -> bool:
        self._seen[action] = self._seen.get(action, 0) + 1
        # 连续 3 次以上同一句话 -> 判定为模型偷懒，后续同类输出不可信
        return self._seen[action] >= 3

    def extract(self, title: str, body: str = "") -> dict:
        """返回 {} 表示失败，调用方应保留原值。"""
        t = (title or "").strip()
        b = (body or "").strip()[:1400]
        if not t and not b:
            return {}
        try:
            raw = self.llm.generate(
                PROMPT.format(title=t[:200], body=b), max_tokens=300)
        except Exception as e:                 # noqa: BLE001
            log.debug("法规要点提取失败: %s", e)
            return {}
        d = _parse_json(raw)
        if not d:
            return {}

        out: dict = {}
        act = str(d.get("action") or "").strip()
        # 小模型爱写套话，挡掉没有信息量的
        if act and 6 <= len(act) <= 90 and not _vague(act):
            if not self._repeated(act):
                out["action"] = act
            else:
                log.warning("模型对多条法规输出同一句 action，已丢弃: %s", act)

        who = str(d.get("who") or "").strip()
        if who in ("个人飞手", "企业", "厂商", "通用"):
            out["who"] = who

        pen = str(d.get("penalty") or "").strip()
        # 罚则必须有具体数字或措施，否则是幻觉
        if pen and re.search(r"\d|吊销|暂扣|拘留|没收|责令", pen) and len(pen) <= 80:
            out["penalty"] = pen

        eff = str(d.get("effective") or "").strip()
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", eff):
            out["effective"] = eff

        kws = d.get("keywords")
        if isinstance(kws, list):
            clean = [str(k).strip() for k in kws
                     if isinstance(k, (str, int)) and 1 < len(str(k).strip()) <= 12]
            if clean:
                out["keywords"] = clean[:5]
        return out


# 套话特征 —— 出现这些说明模型没读出具体内容
_VAGUE = re.compile(
    r"^(规范|加强|促进|保障|明确|完善|推进|推动)[^，。]{0,8}"
    r"(管理|安全|发展|要求|行为)?[。.]?$")


def _vague(s: str) -> bool:
    return bool(_VAGUE.match(s))


def _parse_json(raw: str) -> dict:
    if not raw:
        return {}
    t = raw.strip()
    t = re.sub(r"^\`\`\`(?:json)?|\`\`\`$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:                          # noqa: BLE001
        return {}


# ----------------------------------------------------------------------
# 分类修正
# 现有 category 是纯关键词匹配，"飞行审批"里装的全是适航标准。
# 下面这套规则按**标题里的主导词**判类，优先级从高到低。
_CAT_RULES = [
    # 禁飞通告放最前 —— 它对飞手最要紧，且"管控"这个词在别的
    # 类别标题里几乎不出现，误判代价小。
    # "管控"本身就是强信号 —— 政府通告里出现"XX管控"基本都是
    # 临时限制飞行。不用等"低慢小"一起出现，那个词常被省略。
    ("禁飞通告", r"禁飞|限飞|净空|从严管控|空飘物|低慢小|"
                 r"管控|限制飞行|禁止飞行"),
    # 产业政策 —— 低空经济规划这类对个人飞手没用，
    # 界面给了开关可以整类隐藏（见 main_window 的「隐藏产业政策」）。
    ("产业政策", r"低空经济|发展规划|实施意见|专项行动|试点|示范|"
                 r"产业园|招商引资|政策扶持"),
    ("适航认证", r"适航|型号审定|专用条件|符合性|生产管理"),
    ("空域管理", r"空域|真高|空管|飞行区域|航路|飞行高度"),
    # 下面这几类是**常识**而非「今日新规」—— 实名登记怎么办、
    # 执照怎么考、飞行怎么报批，规则多年不变，天天推没意义。
    # 归到「飞行常识」一节讲清楚"在 X 之后需要 Y 方可飞行"。
    ("实名登记", r"实名登记|国籍登记|登记管理(?:程序|规定|办法)"),
    ("驾驶员资质", r"操控员|驾驶员|执照|训练要求|考试|合格证"),
    ("飞行审批", r"飞行申请|飞行审批|飞行计划|报备|审批流程"),
    ("运行管理", r"运行安全|运行管理|飞行管理|管理规定|条例|规则|办法"),
]

# 归入「飞行常识」的类别 —— 这些不进今日情报流的主列表，
# 而是提炼成"在 X 之后需要 Y 方可飞行"的一句话常识。
COMMON_CATS = ("实名登记", "驾驶员资质", "飞行审批")

# 属于常识类的标题特征（用于把内容也归到常识）
COMMON_PAT = re.compile(
    r"实名登记|国籍登记|激活|操控员|驾驶员|执照|训练|考试|"
    r"合格证|飞行申请|飞行审批|飞行计划|报备|审批流程")

# 正文只在标题完全没线索时才用，且只认这几个高置信度的词
_BODY_RULES = [
    ("禁飞通告", r"禁止[^。]{0,10}飞行|禁飞|临时管控"),
    ("适航认证", r"适航审定|型号合格证"),
    ("驾驶员资质", r"操控员执照|驾驶员执照"),
    ("实名登记", r"实名登记"),
    ("空域管理", r"管制空域|适飞空域"),
]

# "在 X 之后需要 Y 方可飞行" —— 常识条目的标准句式。
# 用户要的是这个格式，不是"某某规定解读"。
FLY_RULE_TMPL = "{when}之后，{who}需要{what}方可飞行"


def fix_category(title: str, body: str = "", old: str = "") -> str:
    """按标题主导词重新判类。

    标题词优先于正文 —— 正文里往往什么都提一句，判出来会飘。
    """
    t = (title or "")
    for name, pat in _CAT_RULES:
        if re.search(pat, t):
            return name
    # 标题没线索时退回正文，但只用高置信度规则 ——
    # 正文什么都提一句，用全套规则判会飘。
    b = (body or "")[:400]
    for name, pat in _BODY_RULES:
        if re.search(pat, b):
            return name
    return old or "运行管理"
