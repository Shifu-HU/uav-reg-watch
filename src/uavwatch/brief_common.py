"""禁飞总结与飞行常识。

两件事：

1. **禁飞总结** —— 用户要的格式是「区域 + 时间 + 来源」三件套，
   一句话说完。原来把整段管控要求抄上去没人看得完。

2. **飞行常识** —— 实名登记、执照、报批这类规则多年不变，
   天天推给用户没意义。提炼成「在 X 之后，你需要 Y 方可飞行」，
   详情指向官网原文。用户要的就是这个句式。
"""

from __future__ import annotations

import re

from .knowledge import NoFlyAlert

# ---------------------------------------------------------------
# 〇、商业内容识别
# ---------------------------------------------------------------
# 用户要的是"我能不能飞、怎么飞"，而低空经济规划、招商、企业
# 评价、物流航线这类是给企业和投资人看的。
#
# 光靠分类字段挡不住 —— 实测 28 条被标成"产业政策"的之外，
# 还有"城市场景物流航线划设规范"这种被错分到运行管理里的。
# 所以这里按**内容特征**再判一次，跟分类字段无关。
_BIZ = re.compile(
    r"产业|低空经济|招商|投资|融资|上市|资本|市值|营收|"
    r"企业|公司|集团|产业园|示范区|试验区|基地建设|"
    r"物流配送|物流航线|外卖|快递配送|载人运输|"
    r"中标|采购|招标|供应|订单|交付|量产|产线|营收|"
    r"市场规?模|市场前景|行业报告|白皮书|峰会|论坛|博览会|展会|"
    r"诚信经营|经营评价|资质等级|评级")

# 但法规里出现这些词是在**规范**商业活动，不是商业宣传，
# 不能一起挡掉。命中就放行。
#
# 注意别把"通知/通告/公告"放进来 —— 那只能说明它是份公文，
# 不代表内容跟飞手有关。《关于同意扩大无人机物流配送应用
# 试点范围的通知》就是典型的公文，但对个人飞手毫无意义。
_BIZ_KEEP = re.compile(
    r"禁止|不得|应当|必须|违法|处罚|罚款|吊销|限期|责令|"
    r"管理规定|管理办法|技术要求|适航|实名登记|执照|操控员|"
    r"飞行高度|空域|禁飞|限飞")


def is_business(title: str, body: str = "") -> bool:
    """这条是不是面向企业/投资人的商业内容。

    标题命中商业词、并且不像法规条文（没有"应当/禁止/处罚"这类
    规范用语），就认为是商业内容。

    宁可漏判也不能误杀 —— 把《无人驾驶航空器飞行管理暂行条例》
    挡掉比多显示几条招商新闻严重得多。
    """
    t = (title or "").strip()
    if not t:
        return False
    if not _BIZ.search(t):
        return False
    # 标题或正文里有规范用语 -> 是法规，不是商业宣传
    if _BIZ_KEEP.search(t) or _BIZ_KEEP.search((body or "")[:400]):
        return False
    return True


# ---------------------------------------------------------------
# 一、禁飞总结
# ---------------------------------------------------------------


def summarize_no_fly(a: NoFlyAlert) -> str:
    """把一条禁飞通告压成「区域 + 时间 + 来源」。

    用户明确要求这个格式 —— 三样齐全他才能判断"跟不关我的事"。
    缺哪样就写"未标注"，不留空 —— 空白看起来像程序坏了。
    """
    area = (a.area or "").strip()
    if not area:
        area = {"全域": "全市全域", "单个地点": "特定区域"}.get(
            a.scope or "", "范围未明确")
    when = (a.when_text or "").strip() or "时间未标注"
    src = _source_short(a.source_name or a.source or "",
                        getattr(a, "official_url", "") or a.url or "",
                        getattr(a, "title", "") or "")
    return f"{area}｜{when}｜{src}"


# 域名 -> 发布单位。来源要显示**谁发的**，不是"从哪搜到的"。
# 搜索引擎名（SearXNG(google cse)）对飞手毫无意义。
_DOMAIN_ORG = [
    (r"caac.gov.cn", "民航局"),
    (r"gov.cn$", ""),                     # 通用政府域名，下面按二级域推
    (r"uom.caac.gov.cn", "民航局 UOM"),
    (r"dji.com", "大疆官方"),
    (r"sz.gov.cn", "深圳市政府"),
    (r"gd.gov.cn", "广东省政府"),
]


# 官方来源的域名特征 —— 政府部门和监管机构。
_OFFICIAL_HOST = re.compile(
    r"\.gov\.cn$|\.gov\.|caac\.gov\.cn|uom\.caac\.gov\.cn|"
    r"\.mil\.cn$|"

    # 官方媒体 / 权威机构
    r"xinhuanet\.com|people\.com\.cn|cctv\.com|chinanews\.com")

# 自媒体 / UGC —— 这些不是"假消息"，但**不能当法规依据**。
# 用户明确说过："以民航局与官方为准"。
_THIRD_HOST = re.compile(
    r"zhihu\.com|zhuanlan\.zhihu|baijiahao\.baidu|sohu\.com|"
    r"toutiao\.com|163\.com|sina\.com\.cn|weixin\.qq\.com|"
    r"mp\.weixin|jianshu\.com|bilibili\.com|douyin\.com|xueqiu\.com|"
    r"360doc|wenku\.|docin\.com|csdn\.net|juejin\.cn")


def trust_of(row: dict) -> str:
    """这条内容的可信度 —— official / media / third。

    数据库里**没有** trust 字段（只有 source 和 url），所以这里
    按域名现推。用户要"以民航局与官方为准"，界面上就必须能
    一眼区分官方通告和自媒体转述。

    推不出来的一律算 media（不吹也不贬）—— 默认当"可参考但
    需核实"，比默认当官方安全得多。
    """
    url = (row.get("url") or "").strip()
    host = ""
    if url:
        try:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
        except Exception:                  # noqa: BLE001
            host = ""
    if host:
        if _OFFICIAL_HOST.search(host):
            return "official"
        if _THIRD_HOST.search(host):
            return "third"
    src = (row.get("source") or "").strip().lower()
    if src in ("caac", "gov", "official"):
        return "official"
    return "media"


def _org_from_url(url: str) -> str:
    """从 URL 域名推发布单位 —— 比 source_name 可靠得多。"""
    from urllib.parse import urlparse
    try:
        host = urlparse(url or "").hostname or ""
    except Exception:                      # noqa: BLE001
        return ""
    host = host.lower()
    if not host:
        return ""
    for pat, name in _DOMAIN_ORG:
        if name and re.search(pat, host):
            return name
    # xxx.gov.cn -> 取 xxx 对应的地区名（有映射就用，没有就保留域名主干）
    m = re.match(r"([a-z0-9-]+).gov.cn$", host)
    if m:
        seg = m.group(1)
        KNOWN = {
            "sz": "深圳市政府", "gz": "广州市政府", "gd": "广东省政府",
            "sh": "上海市政府", "bj": "北京市政府", "tj": "天津市政府",
            "cq": "重庆市政府", "zj": "浙江省政府", "js": "江苏省政府",
        }
        if seg in KNOWN:
            return KNOWN[seg]
        # 县级/市级小站点没有映射表 —— 与其显示裸域名
        # "yncxym.gov.cn"（用户不知道那是哪儿），不如统一写
        # "政府网站"。好歹让人知道这是官方发的，不是自媒体。
        return "政府网站"
    return ""


# 标题里出现的机构名 —— 域名推不出来时的兜底。
# "深圳市人民政府关于…的通告" -> 深圳市政府
_TITLE_ORG = re.compile(
    r"([\u4e00-\u9fa5]{2,8}?(?:省|市|区|县|自治州)?"
    r"(?:人民政府|公安局|民航局|交通运输局|应急管理局|"
    r"空管|管理局|管理委员会|空港经济区管理委员会))")


def _org_from_title(title: str) -> str:
    """从标题里抠发布单位 —— 域名推不出时的兜底。"""
    m = _TITLE_ORG.search(title or "")
    if not m:
        return ""
    name = m.group(1)
    return name[:20]


def _source_short(s: str, url: str = "", title: str = "") -> str:
    """来源显示成**发布单位**。

    优先用 URL 域名推 —— source_name 里存的是搜索引擎名
    （"SearXNG(google cse)"），那个对飞手没有意义。
    域名推不出来时再退回 source_name。
    """
    org = _org_from_url(url)
    if org:
        return org
    org = _org_from_title(title)
    if org:
        return org
    s = (s or "").strip()
    # 搜索引擎名不是来源
    if not s or s.lower().startswith(("searxng", "bing", "google")):
        return "来源未标注"
    for cut in ("办公厅", "办公室", "管理委员会", "管理局"):
        if s.endswith(cut) and len(s) > len(cut) + 2:
            s = s[: -len(cut)]
    return s[:24]


def no_fly_lines(a: NoFlyAlert) -> list[tuple[str, str]]:
    """给晨报用的字段对：区域/时间/来源/要点/查询官网。"""
    out = [("禁飞区域", (a.area or "").strip() or "见通告原文"),
           ("禁飞时间", (a.when_text or "").strip() or "见通告原文"),
           ("发布来源", (a.source_name or a.source or "").strip()
            or "见通告原文")]
    if (a.key_point or "").strip():
        out.append(("要点", a.key_point.strip()))
    return out


# ---------------------------------------------------------------
# 二、飞行常识：「在 X 之后需要 Y 方可飞行」
# ---------------------------------------------------------------

# 常识条目的标准句式。
# {when} 之后，{who} 需要 {what} 方可飞行
_TMPL = "{when}之后，{who}需要{what}方可飞行"


# 这类规则多年不变，硬编码比让模型总结可靠 ——
# 模型会写出"法规要求飞手遵守相关规定"这种废话。
COMMONSENSE = [
    {
        "id": "reg-realname",
        "when": "购买无人机",
        "who": "机主",
        "what": "在民航局 UOM 系统完成**实名登记**并激活",
        "title": "实名登记与激活",
        "why": "未登记飞行属于违规，且 2024 年起新机必须激活后才能起飞。",
        "url": "https://uom.caac.gov.cn",
        "url_text": "民航局民用无人驾驶航空器综合管理平台（UOM）",
        "cat": "实名登记",
    },
    {
        "id": "reg-license",
        "when": "操控轻型以上无人机",
        "who": "飞手",
        "what": "持有相应等级的**操控员执照**",
        "title": "操控员执照",
        "why": "微型、轻型在适飞空域内飞行无需执照；小型及以上必须持证。",
        "url": "https://uom.caac.gov.cn",
        "url_text": "民航局民用无人驾驶航空器综合管理平台（UOM）",
        "cat": "驾驶员资质",
    },
    {
        "id": "reg-approval",
        "when": "在管制空域或超 120 米高度飞行",
        "who": "飞手",
        "what": "提前向空管部门**提交飞行计划并获批**",
        "title": "飞行计划报批",
        "why": "适飞空域内 120 米以下无需申请；超出范围必须先报批。",
        "url": "https://uom.caac.gov.cn",
        "url_text": "民航局民用无人驾驶航空器综合管理平台（UOM）",
        "cat": "飞行审批",
    },
    {
        "id": "reg-120m",
        "when": "在适飞空域内飞行",
        "who": "飞手",
        "what": "将真高控制在 **120 米以内**",
        "title": "真高 120 米上限",
        "why": "超过 120 米即进入管制空域，需另行报批。",
        "url": "https://www.caac.gov.cn",
        "url_text": "中国民用航空局",
        "cat": "空域管理",
    },
]


def commonsense_items(extra_rows: list[dict] | None = None
                      ) -> list[dict]:
    """返回常识条目。

    内置的永远有；从法规库里抓到的**补充**在末尾 ——
    用户可能想知道最近有没有新变化。
    """
    out = []
    for c in COMMONSENSE:
        out.append({
            "id": c["id"],
            "title": c["title"],
            "sentence": _TMPL.format(when=c["when"], who=c["who"],
                                     what=c["what"]),
            "why": c["why"],
            "url": c["url"],
            "url_text": c["url_text"],
            "cat": c["cat"],
        })
    return out


def map_link() -> dict:
    """禁飞区地图 —— 用户让指向 UOM，不要自己画。"""
    return {
        "title": "禁飞区地图",
        "text": "实时禁飞区、适飞空域以民航局 UOM 平台为准，"
                "各地临时管制以当地政府通告为准。",
        "url": "https://uom.caac.gov.cn",
        "url_text": "打开民航局 UOM 平台查看禁飞区地图",
    }


# 来源可信度标签 —— 用户要求禁飞必须标明来源
SOURCE_TAGS = {
    "official": "官方",
    "media": "媒体转述",
    "third": "第三方",
}


def source_tag(trust: str) -> str:
    return SOURCE_TAGS.get((trust or "").lower(), "来源未标注")
