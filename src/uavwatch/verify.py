"""消息核验 —— 只信官方源。

晨报里的每条禁飞预警都要过这里，避免把自媒体转述当成官方通告。
原则：
    1. 官方域名直采 -> 可信
    2. 官方转述(新闻媒体引用政府通告) -> 待核验，标注来源
    3. 自媒体/聚合站 -> 低可信，不进"重点"
    4. 能直接打开官方原文的 -> 回抓确认，最可信

多花几分钟换准确性，比推一条假禁飞划算。
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

log = logging.getLogger("uavwatch.verify")

# ----------------------------------------------------------------------
# 官方域名 —— 只有这些算"一手来源"
# ----------------------------------------------------------------------
OFFICIAL_HOSTS = (
    # 民航系统
    "caac.gov.cn", "atmb.caac.gov.cn", "ccaonline.cn",
    # 国家与部委
    "gov.cn", "www.gov.cn", "mod.gov.cn",
    # 公安/应急
    "mps.gov.cn", "119.gov.cn",
    # 深圳市（sznews.com 是深圳报业集团旗下媒体，不是政府门户，故不在此列）
    "sz.gov.cn", "ga.sz.gov.cn", "sf.sz.gov.cn", "jtys.sz.gov.cn",
    "yjgl.sz.gov.cn", "gd.gov.cn", "gdga.gd.gov.cn",
    # 地方通用规则见 _is_official_host
)

# 官方域名后缀特征：xxx.gov.cn / 各级政府门户
# 注意：.gov.cn 是硬标准 —— 只有政府机构才能注册，因此可靠。
# 媒体域名(如 sznews.com)即使常发官方通稿，也不算一手来源。
_GOV_RE = re.compile(r"(^|\.)(gov\.cn|gov\.hk)$")
# 深圳的区政府门户形如 ft.sz.gov.cn / baoan.gov.cn
_SZ_GOV_RE = re.compile(r"^[a-z]+\.sz\.gov\.cn$")

# 媒体 —— 引用官方通告，可信但不能当一手
MEDIA_HOSTS = (
    "xinhuanet.com", "news.cn", "people.com.cn", "cctv.com", "cnr.cn",
    "chinanews.com", "thepaper.cn", "southcn.com", "ycwb.com",
    "bjnews.com.cn", "caixin.com", "yicai.com", "21jingji.com",
    "stcn.com", "cnstock.com", "jiemian.com", "nbd.com.cn",
    "sznews.com", "szdaily.com", "carnoc.com",
)

# 自媒体/聚合 —— 只能当线索，不能当依据
LOW_TRUST_HOSTS = (
    "toutiao.com", "zhihu.com", "sohu.com", "baidu.com", "360kuai.com",
    "haokan.", "weixin.qq.com", "douyin.com", "kuaishou.com",
    "163.com", "sina.com.cn", "sina.cn", "qq.com", "ifeng.com",
    "jrj.com.cn", "xueqiu.com", "bilibili.com", "xiaohongshu.com",
    "uav-bao.com", "maigoo.com", "bendibao.com", "ai.so.com",
)

# 可信等级
TRUST_OFFICIAL = "官方"
TRUST_MEDIA = "媒体转述"
TRUST_LOW = "自媒体"
TRUST_UNKNOWN = "未知来源"

_TRUST_ORDER = {TRUST_OFFICIAL: 3, TRUST_MEDIA: 2, TRUST_UNKNOWN: 1,
                TRUST_LOW: 0}


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:                          # noqa: BLE001
        return ""


def is_official_host(host: str) -> bool:
    if not host:
        return False
    # 先排除媒体/自媒体 —— 有些媒体域名与官方域名形近(如 sznews vs sz.gov.cn)
    for h in MEDIA_HOSTS:
        if host == h or host.endswith("." + h):
            return False
    for h in LOW_TRUST_HOSTS:
        if h in host:
            return False
    if host in OFFICIAL_HOSTS:
        return True
    if host.endswith(".gov.cn") or host == "gov.cn":
        return True
    if _GOV_RE.search(host):
        return True
    if _SZ_GOV_RE.match(host):
        return True
    for h in OFFICIAL_HOSTS:
        if host == h or host.endswith("." + h):
            return True
    return False


def classify_trust(url: str) -> str:
    """判断一条链接的可信等级。"""
    host = host_of(url)
    if not host:
        return TRUST_UNKNOWN
    if is_official_host(host):
        return TRUST_OFFICIAL
    for h in MEDIA_HOSTS:
        if host == h or host.endswith("." + h):
            return TRUST_MEDIA
    for h in LOW_TRUST_HOSTS:
        if h in host or h in url:
            return TRUST_LOW
    return TRUST_UNKNOWN


def trust_rank(level: str) -> int:
    return _TRUST_ORDER.get(level, 1)


# ----------------------------------------------------------------------
# 官方原文定位
# ----------------------------------------------------------------------
# 从正文里找"据XX发布""XX通告"提到的官方机构，用于给媒体转述定级
_OFFICIAL_MENTION = re.compile(
    r"(民航局|民航中南地区管理局|深圳市人民政府|深圳市公安局|深圳市交通运输局|"
    r"深圳市应急管理局|广东海事局|中国人民解放军|空中交通管制委员会|"
    r"市人民政府|公安局|交通运输局)")


def verify_alert(alert, fetcher=None, timeout: float = 8.0) -> dict:
    """核验一条禁飞预警。

    返回:
        ok          是否可作为"重点"推送
        trust       可信等级
        reason      人话解释为什么可信/不可信
        official_url 如果能定位到官方原文
    """
    url = getattr(alert, "url", "") or ""
    trust = classify_trust(url)
    title = getattr(alert, "title", "") or ""
    snippet = (getattr(alert, "snippet", "") or "")

    reason = ""
    official_url = ""

    if trust == TRUST_OFFICIAL:
        reason = f"官方源直采（{host_of(url)}）"
        return {"ok": True, "trust": trust, "reason": reason,
                "official_url": url}

    if trust == TRUST_LOW:
        return {"ok": False, "trust": trust,
                "reason": f"自媒体/聚合站转述（{host_of(url)}），不作为依据",
                "official_url": ""}

    # 媒体转述：看正文有没有点明官方机构
    blob = title + " " + snippet
    m = _OFFICIAL_MENTION.search(blob)
    if m:
        reason = f"{TRUST_MEDIA}，引述「{m.group(1)}」"
        # 回抓媒体页面，尝试定位官方原文链接。
        # 找不到官方原文时只做"参考"不进"重点" —— 用户是深圳飞手，
        # 一条他没听说过的"禁飞"若来自二手转述，会直接误导他。
        if fetcher is not None:
            got = _find_official(alert, fetcher, timeout)
            if got:
                official_url = got
                reason += "；已定位官方原文"
                return {"ok": True, "trust": TRUST_MEDIA, "reason": reason,
                        "official_url": official_url}
        reason += "；未定位到官方原文，仅供参考"
        return {"ok": False, "trust": TRUST_MEDIA, "reason": reason,
                "official_url": ""}

    return {"ok": False, "trust": trust,
            "reason": f"{TRUST_MEDIA}但未点明官方机构（{host_of(url)}）",
            "official_url": ""}


_OFFICIAL_LINK = re.compile(
    r'https?://[^\s"\'<>]*?(?:gov\.cn|sz\.gov\.cn)[^\s"\'<>]*')


def _find_official(alert, fetcher, timeout: float) -> str:
    """回抓媒体页面，找里面指向官方源的链接。"""
    url = getattr(alert, "url", "") or ""
    if not url:
        return ""
    try:
        html = fetcher.get_text(url, timeout=timeout)
    except Exception:                          # noqa: BLE001
        return ""
    if not html:
        return ""
    for m in _OFFICIAL_LINK.finditer(html[:200000]):
        cand = m.group(0)
        if is_official_host(host_of(cand)):
            return cand
    return ""


def verify_all(alerts: list, fetcher=None, timeout: float = 8.0,
               max_checks: int = 10) -> None:
    """就地核验一批预警，把结果写回对象。

    max_checks 限制回抓次数 —— 每条都要联网，控制在可接受范围。
    """
    checks = 0
    for a in alerts:
        # 本地相关的才值得花时间核验
        if not getattr(a, "in_my_area", False):
            a.trust = classify_trust(getattr(a, "url", ""))
            a.verified = a.trust == TRUST_OFFICIAL
            a.verify_reason = f"外地通告（{a.trust}）"
            continue
        use_fetcher = fetcher if checks < max_checks else None
        res = verify_alert(a, use_fetcher, timeout)
        a.trust = res["trust"]
        a.verified = res["ok"]
        a.verify_reason = res["reason"]
        a.official_url = res["official_url"]
        if use_fetcher is not None:
            checks += 1
