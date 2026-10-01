"""用本地小模型从通告原文里抽取要点，并判断真假与是否与本地相关。

为什么需要它：
    搜索摘要在 90 字左右就被截断，"东起新洲路沿线、西至…"这种四至
    描述往往只给一半。用户是深圳飞手，看到半截范围比看到"范围见原文"
    更危险 —— 他可能以为自己那片不在范围内。

小模型（qwen3.5:0.8b）在这里够用，因为任务是**抽取**而不是**推理**：
它只需要在一段已经确定是禁飞通告的文本里，把时间、范围、禁飞对象
和是否只涉及部分道路挑出来。给它一个严格的 JSON 模板即可。

代价：首次全量核查要多花几分钟。用户明确表示可以接受
（"第一次开机哪怕花十分钟都行"）。
"""

from __future__ import annotations

import json
import logging
import re

log = logging.getLogger("uavwatch.llmcheck")

# ----------------------------------------------------------------------
# 提示词 —— 越具体，小模型越不容易跑偏
# ----------------------------------------------------------------------
PROMPT = """你是无人机法规助理，帮深圳的飞手判断一条禁飞通告。

只输出 JSON，不要解释，不要 markdown 代码块。格式：
{{"is_notice": true/false, "scope": "全域/部分区域/单个地点/不清楚", \
"area": "禁飞范围，越具体越好，没有则空字符串", \
"start": "YYYY-MM-DD 或 空", "end": "YYYY-MM-DD 或 空", \
"objects": "禁飞对象", "key_point": "一句话说清对飞手的影响"}}

判断规则：
- is_notice：这条是不是**政府的禁飞/限飞通告**？
  政策解读、行业标准、培训广告、新闻综述都填 false。
- scope 的判定要看**有没有给出具体边界**：
  · 只有原文明确说"全市""全域""整个XX市"才填"全域"
  · 出现路口、路段、街道、公园、场馆、广场、"XX路至XX路"、
    "由XX围合"这类**具体地理边界** -> 填"单个地点"
  · 只点名了几个区/几个镇 -> 填"部分区域"
  · 标题写"这一区域""相关区域"但没说全市 -> 最多"部分区域"
  · 看不出来 -> 填"不清楚"
  **不确定时不要填"全域"** —— 把局地管控说成全市禁飞会让飞手
  误判，比说"不清楚"危险得多。
- area：抄原文里的范围描述，不要自己概括，不要编造。
  有具体四至/路段就抄四至；标题说"这一区域"但正文有边界，
  以正文边界为准。**不要把标题抄成 area**，标题不是范围。
- 日期只填原文明确写出的，没有就留空字符串。
  "即日起至X月X日"表示**从现在起就生效**，不是从X月X日才开始。

文本：
{text}
"""


class BriefChecker:
    """用本地模型核查禁飞通告的要点。"""

    def __init__(self, llm):
        self.llm = llm

    # ------------------------------------------------------------------
    def check(self, alert, text: str = "", timeout_ok: bool = True) -> dict:
        """核查一条预警。text 为空时用 alert 自带的摘要。

        返回 {} 表示模型不可用或没给有效结果 —— 调用方应保留原值，
        绝不能因为模型失败而丢失已有的准确信息。
        """
        body = (text or getattr(alert, "snippet", "") or "").strip()
        title = getattr(alert, "title", "") or ""
        if not body and not title:
            return {}
        # 提示词塞太长小模型会走神，正文截到 1200 字足够
        payload = (title + "\n" + body)[:1200]
        try:
            raw = self.llm.generate(
                PROMPT.format(text=payload), max_tokens=320)
        except Exception as e:                 # noqa: BLE001
            log.debug("模型核查失败: %s", e)
            return {}
        data = _parse_json(raw)
        if not data:
            return {}

        out: dict = {}
        # 是否为真通告 —— 这是最有价值的一项：能挡掉解读类文章
        if isinstance(data.get("is_notice"), bool):
            out["is_notice"] = data["is_notice"]

        scope = str(data.get("scope") or "").strip()
        # 兜底：模型说"全域"，但原文含四至/路段这类具体边界时，
        # 强制降为"单个地点"。小模型在这里很容易判错，而判错的
        # 代价是飞手以为全市禁飞 —— 必须用规则兜住。
        if scope == "全域" and _has_concrete_boundary(payload):
            log.info("模型判全域但原文有具体边界，降级为单个地点: %s",
                     payload[:40])
            scope = "单个地点"
        if scope in ("全域", "部分区域", "单个地点", "不清楚"):
            out["scope"] = scope

        area = str(data.get("area") or "").strip()
        # 小模型爱编造，只接受能在原文里找到依据的范围。
        # 还要过一遍质量检查 —— 模型经常给出被截断的半截四至
        # （"东起新洲路沿线、西至…"），这种比不填更危险：
        # 飞手会以为西边界就是那条路。宁可空着提示"见原文"。
        if area and _grounded(area, payload) and _usable_area(area):
            out["area"] = area[:120]

        kp = str(data.get("key_point") or "").strip()
        if kp and len(kp) >= 8 and _grounded(kp, payload, loose=True):
            out["key_point"] = kp[:120]

        for k in ("start", "end"):
            v = str(data.get(k) or "").strip()
            if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", v):
                out[k] = v
        return out


# 具体地理边界的特征 —— 出现这些说明是局地管控而非全域
_BOUNDARY = re.compile(
    r"(东起|西至|南起|北至|以南|以北|以东|以西|"
    r"沿线|围合|起至[^。]{0,6}(?:路|道|街)|"
    r"[\u4e00-\u9fa5]{2,6}(?:路|大道|街道|广场|公园|体育馆|体育场|"
    r"会展中心|机场|口岸|车站|码头|景区))")


def _has_concrete_boundary(text: str) -> bool:
    """原文是否给出了具体地理边界。"""
    if not text:
        return False
    hits = _BOUNDARY.findall(text)
    # 单个"XX路"可能是机构名里的字，要 2 处以上才认定是范围描述
    return len(hits) >= 2


# ----------------------------------------------------------------------
def _usable_area(area: str) -> bool:
    """模型给的范围能不能直接用。

    三类要挡掉：
      1. 带省略号的 —— 说明原文就截断了，四至不完整
      2. 含方位词但四至不成对 / 方位词后没地名的
      3. 没有地名的 —— 那是条款或义务描述，不是地理范围
    """
    from .knowledge import _complete_bounds, _has_geo
    import re as _re
    if "..." in area or "…" in area:
        return False
    if _re.search(r"[东西南北][起至]", area) and not _complete_bounds(area):
        return False
    return _has_geo(area)


def _grounded(s: str, source: str, loose: bool = False) -> bool:
    """检查 s 是否真的来自 source，防止模型自己编。

    判据：s 里超过 40% 的 2 字片段能在原文找到，就算有依据。
    小模型会改写措辞，所以不能要求逐字匹配。
    """
    s = re.sub(r"[\s，。、；：（）()【】\"']", "", s)
    src = re.sub(r"[\s，。、；：（）()【】\"']", "", source)
    if not s:
        return False
    if s in src:
        return True
    grams = [s[i:i + 2] for i in range(0, max(len(s) - 1, 1), 2)]
    if not grams:
        return loose
    hit = sum(1 for g in grams if g in src)
    ratio = hit / len(grams)
    return ratio >= (0.25 if loose else 0.4)


def _parse_json(raw: str) -> dict:
    """从模型输出里抠出 JSON —— 小模型常带前后废话或代码块围栏。"""
    if not raw:
        return {}
    t = raw.strip()
    t = re.sub(r"^```(?:json)?|^```|\`\`\`$", "", t).strip()
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:                          # noqa: BLE001
        return {}


def apply_check(alert, res: dict) -> None:
    """把核查结果写回预警对象。**只补强，不覆盖已有准确信息。**"""
    if not res:
        return
    # 模型判定不是通告 -> 降级，不再作为重点
    if res.get("is_notice") is False:
        alert.not_notice = True

    scope = res.get("scope")
    if scope:
        alert.scope = scope
        # "只管控几条路"却被当成全域，是最容易误导飞手的情形。
        # 标出真实范围级别，渲染时据此提示。
        if scope in ("部分区域", "单个地点") and not alert.area:
            alert.area = res.get("area") or ""

    # 模型给的时段只在原位没提到时才采用（原文提取优先）
    if not alert.start and res.get("start"):
        try:
            from datetime import date as _d
            y, m, dd = (int(x) for x in res["start"].split("-"))
            alert.start = _d(y, m, dd)
            alert.raw_when = res["start"]
        except Exception:                      # noqa: BLE001
            pass
    if not alert.end and res.get("end"):
        try:
            from datetime import date as _d
            y, m, dd = (int(x) for x in res["end"].split("-"))
            alert.end = _d(y, m, dd)
        except Exception:                      # noqa: BLE001
            pass

    if res.get("key_point"):
        alert.key_point = res["key_point"]
