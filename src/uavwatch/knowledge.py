"""飞行常识知识库 + 临时禁飞预警。

常识来自 knowledge.yaml（内置，不靠搜索）。
临时禁飞从已收录的禁飞通告里提取「地区 + 时间 + 范围」，
命中用户所在地时在晨报里加重强调。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import yaml

log = logging.getLogger("uavwatch.knowledge")

# 默认知识库位置（源码 ../src/uavwatch/ 或打包后 _internal/uavwatch/）
_HERE = Path(__file__).resolve().parent


# ======================================================================
# 常识知识库
# ======================================================================
class KnowledgeBase:
    def __init__(self, path: Path | None = None):
        self.path = path or (_HERE / "knowledge.yaml")
        self.items: list[dict] = []
        self.load()

    def load(self) -> None:
        try:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or []
            self.items = [it for it in raw if isinstance(it, dict) and it.get("title")]
            log.info("知识库载入 %d 条: %s", len(self.items), self.path)
        except Exception as e:                 # noqa: BLE001
            log.warning("知识库载入失败 %s: %s", self.path, e)
            self.items = []

    # ------------------------------------------------------------------
    def by_category(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for it in self.items:
            out.setdefault(it.get("category") or "其他", []).append(it)
        return out

    def essentials(self, limit: int = 6) -> list[dict]:
        """晨报固定输出的核心常识 —— 高度/空域优先。"""
        order = {"常识": 0, "地区": 1, "建议": 2}
        items = sorted(self.items, key=lambda x: order.get(x.get("level") or "", 9))
        # 高度和空域永远排最前 —— 飞手最需要
        hot = [i for i in items if (i.get("category") or "") in ("高度", "空域")]
        rest = [i for i in items if i not in hot]
        return (hot + rest)[:limit]

    def region_notes(self, city: str = "", province: str = "") -> list[dict]:
        """与用户所在地相关的常识。"""
        out = []
        for it in self.items:
            blob = (it.get("title", "") + it.get("detail", "")
                    + " ".join(it.get("keywords") or []))
            if city and city in blob:
                out.append(it)
            elif province and province in blob and it not in out:
                out.append(it)
        return out

    def search(self, text: str) -> list[dict]:
        text = (text or "").strip()
        if not text:
            return []
        return [it for it in self.items
                if text in (it.get("title", "") + it.get("short", "")
                            + " ".join(it.get("keywords") or []))]


# ======================================================================
# 临时禁飞提取
# ======================================================================
# 禁飞类信号词
NOFLY_WORDS = ("禁飞", "禁飞区", "禁飞通告", "临时禁飞", "限飞", "禁飞令",
               "禁止飞行", "净空", "禁售", "停飞", "管控")

# 时间范围写法：2026年8月1日至8月15日 / 8月1日-8月15日 / 2026.8.1 起
_DATE_PATTERNS = [
    # 2026年8月1日至8月15日
    re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
               r"\s*[至到\-—~～]\s*(?:(20\d{2})\s*年)?\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    # 8月1日至8月15日
    re.compile(r"(?<![\d年])(\d{1,2})\s*月\s*(\d{1,2})\s*日"
               r"\s*[至到\-—~～]\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    # 2026年8月1日起 / 8月1日起正式实施
    re.compile(r"(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起"),
    # 2026-08-01 至 2026-08-15
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})\s*[至到\-—~～]\s*"
               r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
    # 即日起至11月23日 —— "即日起"意味着开始时间就是通告发布时点，
    # 由调用方结合上文日期补全，这里只返回结束日期。
    # 至11月底 / 至12月底（月末）
    re.compile(r"[至到]\s*(\d{1,2})\s*月\s*底"),
    # 8月26日12至24时 —— 时段的"至"不是日期分隔，要单独识别为单日
    re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:\d{1,2})\s*[至到]\s*\d{1,2}\s*时"),
]

# 地区词（省级 + 主要城市 + 区）
_REGION_WORDS = [
    "北京", "上海", "广州", "深圳", "天津", "重庆", "杭州", "南京", "成都",
    "武汉", "西安", "苏州", "郑州", "长沙", "青岛", "宁波", "东莞", "佛山",
    "合肥", "福州", "厦门", "济南", "沈阳", "大连", "昆明", "哈尔滨", "南昌",
    "贵阳", "南宁", "太原", "石家庄", "兰州", "海口", "乌鲁木齐", "呼和浩特",
    "银川", "西宁", "拉萨", "珠海", "中山", "惠州", "无锡", "常州", "温州",
    "嘉兴", "泉州", "烟台", "潍坊", "徐州", "绍兴", "台州", "保定", "洛阳",
    "广东", "江苏", "浙江", "山东", "河南", "四川", "湖北", "湖南", "福建",
    "安徽", "河北", "陕西", "江西", "辽宁", "云南", "广西", "山西", "贵州",
    "吉林", "黑龙江", "内蒙古", "新疆", "甘肃", "海南", "宁夏", "青海", "西藏",
]

# 深圳的区（用户所在地细化）
_SHENZHEN_DISTRICTS = ["福田", "罗湖", "南山", "盐田", "宝安", "龙岗", "龙华",
                       "坪山", "光明", "大鹏"]


def _mk_date(y, m, d) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except Exception:                      # noqa: BLE001
        return None


_DATE_ANY = [
    re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(?<![\d年])(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})"),
]


def _first_date(text: str, year: int) -> tuple[date | None, date | None, str]:
    """从文字里抓第一个出现的日期。"""
    text = text or ""
    best: tuple[int, date, str] | None = None
    for pat in _DATE_ANY:
        for m in pat.finditer(text):
            g = m.groups()
            if len(g) == 3:
                d = _mk_date(g[0], g[1], g[2])
            else:
                d = _mk_date(year, g[0], g[1])
            if d and (best is None or m.start() < best[0]):
                best = (m.start(), d, m.group(0))
    if best:
        return best[1], None, best[2]
    return None, None, ""


def extract_window(text: str, fallback_year: int | None = None) -> tuple[date | None, date | None, str]:
    """从一段文字里提取禁飞时间窗。

    返回 (开始日期, 结束日期, 原文片段)。识别不到返回 (None, None, "")。

    特例："即日起至11月23日" 这种写法里，"即日起"本身不带日期，
    真正的开始时间是句子里更早出现的那个日期（通常是通告发布日）。
    """
    text = text or ""
    year = fallback_year or date.today().year

    # "即日起" 特判：找"即日起"前面最近的日期当开始，后面最近的当结束
    m_im = re.search(r"即日起\s*[至到]", text)
    if m_im:
        head, tail = text[:m_im.start()], text[m_im.end():]
        s2, _, _ = _first_date(head, year)
        e2, _, _ = _first_date(tail, year)
        if e2:
            # "即日起"= 通告发布即生效。前面找不到日期时，开始就是
            # 今天 —— 之前返回 (e2, None) 会把结束日当成开始日，
            # 进行中的禁飞被误判成"还没开始"，排序与级别全错
            # （用户抓到的 7.20 深圳管控正是这个表现）。
            if s2 is None:
                return date.today(), e2, m_im.group(0) + f" ({date.today()}~{e2})"
            if s2 == e2:
                return e2, None, m_im.group(0) + f" (至 {e2})"
            return s2, e2, m_im.group(0) + f" ({s2}~{e2})"

    for pat in _DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        g = m.groups()
        if len(g) == 6:                          # 带年份的范围
            y1 = g[0] or year
            s = _mk_date(y1, g[1], g[2])
            e = _mk_date(g[3] or y1, g[4], g[5])
        elif len(g) == 4:                        # 无年份的范围
            s = _mk_date(year, g[0], g[1])
            e = _mk_date(year, g[2], g[3])
        elif len(g) == 3:                        # 单日（含"即日起至X月X日"）
            s = _mk_date(g[0] or year, g[1], g[2])
            e = None
        elif len(g) == 1:                        # "至11月底"
            import calendar
            m_ = int(g[0])
            if 1 <= m_ <= 12:
                s = _mk_date(year, m_, 1)
                e = _mk_date(year, m_, calendar.monthrange(year, m_)[1])
            else:
                continue
        elif len(g) == 2:                        # "8月26日12至24时" -> 单日
            s = _mk_date(year, g[0], g[1])
            e = None
            if pat is _DATE_PATTERNS[2]:         # "X月X日起" 也可能只有 2 组
                s = _mk_date(year, g[0], g[1])
        else:
            continue
        if s:
            # 开始晚于结束 = 匹配错位(比如"7月16日…至11月23日"被错配成
            # 7月19日~7月16日)，宁可返回单日也不要给出错误区间。
            if e and e < s:
                log.debug("时间区间倒置，丢弃结束日期: %s ~ %s (原文 %r)",
                          s, e, m.group(0))
                e = None
            return s, e, m.group(0)
    return None, None, ""


# 禁飞范围描述词 —— 有这些说明通告给出了具体地理边界
AREA_MARKERS = ("围合区域", "沿线", "所组成", "范围", "区域内", "全域",
                "周边", "一带", "以东", "以西", "以南", "以北", "起至")

# 地理特征词 —— 真正的范围描述一定含这些(路、区、街道…)
GEO_WORDS = ("路", "道", "街", "区", "市", "县", "镇", "村", "公园",
             "广场", "中心", "机场", "口岸", "站", "湖", "山", "河", "湾")


def _has_geo(s: str) -> bool:
    return any(g in s for g in GEO_WORDS)


def extract_area(text: str, limit: int = 130) -> str:
    """从通告里抠出禁飞范围的描述句。

    例如"对东起新洲路沿线、西至农林路沿线…所组成的围合区域进行从严管控"
    —— 这段比标题有用得多，飞手需要知道自己那片在不在里面。
    """
    text = (text or "").replace("\n", " ")
    # "南山区行政区域全境" 这种本身就是完整范围 —— 它不含
    # "沿线/围合/范围内" 这类描述词，光靠 AREA_MARKERS 会漏掉。
    if _ADMIN_FULL.search(text):
        m_adm = _ADMIN_FULL.search(text)
        return m_adm.group(0).strip()[:limit]
    if not any(m in text for m in AREA_MARKERS):
        return ""
    # 带省略号说明原文被截断了。四至描述被截一半会误导飞手
    # （"东起新洲路沿线、西至" 看起来像边界，其实缺了西边），
    # 所以截断处之后的内容直接丢弃，后续由完整性校验决定要不要。
    truncated = False
    if "..." in text or "…" in text:
        text = re.split(r"\.\.\.|…", text)[0]
        truncated = True
    # 优先"东起…西至…"这种四至描述，最精确。
    # 但四至必须**成对出现**才可信 —— 有"东起"无"西至"说明被截断了。
    m = re.search(r"(东起[^。；]{6,120}?)(?:区域|以内|范围|$)", text)
    if m and _has_geo(m.group(1)):
        seg = m.group(1).strip()
        if _complete_bounds(seg) and not truncated:
            return seg[:limit]
        log.debug("四至描述不完整，丢弃: %r", seg[:40])

    # "对…区域"句式 —— 必须含地理特征，否则会把标题本身当成范围
    for m in re.finditer(r"(对[^。；]{6,120}?区域)", text):
        seg = m.group(1)
        # 剔除"对相关区域低空飞行活动实施从严管控的通告"这类公告标题
        if _has_geo(seg) and "通告" not in seg and "管控的通告" not in seg:
            return seg.strip()[:limit]

    # 退而求其次：含范围词 + 地理特征词的短句。
    # 要挡掉条款段落 —— "三、管控要求 在上述管控时段和区域内…"
    # 这种是规定条款，不是范围描述。
    for seg in re.split(r"[。；\n]", text):
        seg = seg.strip()
        if not (any(k in seg for k in AREA_MARKERS) and _has_geo(seg)
                and "通告" not in seg and 6 <= len(seg) <= limit
                and not _is_clause(seg)
                and not _is_prose(seg)):
            continue
        # 含方位词的段落同样要过完整性检查 —— 否则
        # "东起新洲路沿线、西至" 会从这条路径漏出去。
        if re.search(r"[东西南北][起至]", seg) and not _complete_bounds(seg):
            log.debug("范围段落四至不完整，跳过: %r", seg[:40])
            continue
        return seg
    return ""


# 条款段落特征 —— 这些是"怎么执行"，不是"哪里禁飞"
_CLAUSE = re.compile(
    r"^(?:[一二三四五六七八九十]+[、.）)]|\d+[、.）)])"      # 编号开头
    r"|在[上述]{0,3}(?:管控)?(?:时段|期间|区域)内"           # 引用上文
    r"|除经.*(?:批准|批准).*外"                              # 例外条款
    r"|禁止任何单位|应当|不得|违反|处罚|法律责任的")


# 解读文章的句式 —— 这些是评论/说明，不是地理范围。
# 长句 + 评价性词汇 = 正文段落，绝不能当成"禁飞范围"显示，
# 否则飞手看到的就是一大段看不懂的话。
_PROSE = re.compile(
    r"展现了|体现了|标志着|意味着|有效平衡|精准施策|包容审慎|"
    r"既.{0,12}又|不仅.{0,12}而且|通过.{0,16}界定|作出了明确|"
    r"本标准|本规范|本规定|以下简称|以上所称")


def _is_prose(seg: str) -> bool:
    """判断是不是文章段落（而非范围描述）。"""
    if _PROSE.search(seg):
        return True
    # 范围描述不会这么长，也不会有一堆顿号列举。
    # 40 字以上基本就是段落了。
    if len(seg) > 40:
        return True
    return False


def _is_clause(seg: str) -> bool:
    """判断是不是规定条款而不是范围描述。"""
    if _CLAUSE.search(seg):
        return True
    # 把"要求谁做什么"当成禁飞范围会严重误导 ——
    # "请无人机厂商配合政府工作…" 是给厂商的义务，不是地理边界。
    if _ACTION_TEXT.search(seg):
        return True
    # 范围描述必须落在地理上；没有地名的一律不认。
    return not _has_geo(seg)


# "XX区/县/市 行政区域全境" —— 一整个行政区，本身就是完整范围
_ADMIN_FULL = re.compile(
    r"[\u4e00-\u9fa5]{2,8}(?:区|县|市|镇|街道)"
    r"(?:行政区域)?(?:全境|全域|范围内全部|全部区域)")


# "要求谁做什么"的句式 —— 这些是义务条款，不是范围
_ACTION_TEXT = re.compile(
    r"请[^。；]{0,10}(?:厂商|单位|公司|市民|飞手|用户)[^。；]{0,10}(?:配合|遵守|执行|注意)"
    r"|配合政府|划设电子围栏|加强违规飞行|技术要求|应当遵守|请登录|申报|报备" )


# 四至配对 —— 完整的围合描述必须东西南北成对
_BOUND_PAIRS = (("东起", "西至"), ("南起", "北至"),
                ("东至", "西至"), ("南至", "北至"))


def _complete_bounds(seg: str) -> bool:
    """四至是否完整。

    光看方位词在不在不够 —— "东起新洲路沿线、西至" 两个词都在，
    但"西至"后面是空的，这种半截描述比没有更危险：飞手会以为
    西边界就是那条路。所以每个方位词后面必须跟着实际地名。
    """
    found = []
    for word in ("东起", "东至", "西起", "西至", "南起", "南至",
                 "北起", "北至"):
        i = seg.find(word)
        if i < 0:
            continue
        rest = seg[i + len(word):]
        # 后面至少要有一个"路/道/街/区/…"这类地理词才算有内容
        if not _has_geo(rest[:12]):
            return False                     # 方位词后面是空的
        found.append(word)

    has_east = any(w in found for w in ("东起", "东至"))
    has_west = any(w in found for w in ("西起", "西至"))
    has_south = any(w in found for w in ("南起", "南至"))
    has_north = any(w in found for w in ("北起", "北至"))
    if has_east != has_west:
        return False
    if has_south != has_north:
        return False
    return has_east or has_south


def extract_regions(text: str) -> list[str]:
    """从文字里找出地区词。"""
    text = text or ""
    out: list[str] = []
    for w in _REGION_WORDS:
        if w in text and w not in out:
            out.append(w)
    # 深圳的区要单独抓，用于"是否在你辖区内"的判断
    for d in _SHENZHEN_DISTRICTS:
        if d + "区" in text or ("深圳" in text and d in text):
            tag = f"深圳{d}"
            if tag not in out:
                out.append(tag)
    return out


# 标题里出现这些词才算"禁飞/限飞通告"。
# 注意：'管控' '净空' 太宽(会命中政策解读、培训通知)，只在标题里算数。
_TITLE_STRONG = ("禁飞", "限飞", "禁飞区", "禁飞通告", "临时禁飞", "禁飞令",
                 "禁止飞行", "停飞", "禁售")
_TITLE_WEAK = ("净空", "管控", "通告", "管制")


def is_no_fly(row: dict) -> bool:
    """标题里必须出现明确的禁飞信号词。

    只用 summary/ai_summary 会被政策解读类文章污染 —— 比如
    《分布式操作运行等级划分》正文提到禁飞区，就被误判成禁飞通告。
    """
    title = row.get("title") or ""
    if any(w in title for w in _TITLE_STRONG):
        return True
    # 弱信号词必须与"飞行/无人机"同现，且不能是政策解读类
    if any(w in title for w in _TITLE_WEAK):
        if ("无人机" in title or "飞行" in title or "航空器" in title):
            return not any(x in title for x in
                           ("划分", "等级", "办法", "规定", "标准", "培训",
                            "问答", "解读", "介绍", "知识", "有哪些", "了解"))
    return False


class NoFlyAlert:
    """一条临时禁飞预警。"""

    __slots__ = ("title", "url", "source_name", "regions", "start", "end",
                 "raw_when", "in_my_area", "days_until", "active_now", "row",
                 "nationwide", "area", "snippet",
                 "trust", "verified", "verify_reason", "official_url",
                 "scope", "key_point", "not_notice")

    def __init__(self, row: dict, city: str = "", province: str = "",
                 today: date | None = None):
        today = today or date.today()
        self.row = row
        self.title = row.get("title") or ""
        # 摘要(content)往往比标题含更多信息 —— 抓不到正文时靠它。
        # region 不能混进 blob —— 它会被 extract_area 当成范围文本的一部分，
        # 出现"东起新洲路沿线… 全国"这种拼接错误。
        blob = " ".join(str(row.get(k) or "") for k in
                        ("title", "summary", "content", "ai_summary"))
        # content 是原始检索摘要，信息量最大；单独拼一份用于时段/范围提取
        raw_blob = " ".join(str(row.get(k) or "") for k in
                            ("title", "summary", "content"))
        if raw_blob.strip():
            blob = raw_blob + " " + blob
        self.url = row.get("url") or ""
        self.source_name = row.get("source_name") or row.get("source") or ""

        self.regions = extract_regions(blob)
        # 禁飞范围逐字段单独提取 —— 拼在一起的 blob 会跨字段接出
        # "东起新洲路沿线、西至 ... 深圳这一"这种脏串。
        # 取最长的那个候选，通常也是最完整的。
        _cands = []
        for _k in ("content", "summary", "ai_summary", "title"):
            _v = str(row.get(_k) or "")
            if _v:
                _a = extract_area(_v)
                if _a:
                    _cands.append(_a)
        self.area = max(_cands, key=len) if _cands else ""
        # 摘要优先级：原始检索摘要 > AI 概括。
        # AI 概括只有 90 字，常把"即日起至11月23日"这类关键时间压掉，
        # 而核验和时段提取恰恰依赖它。
        raw_snip = (row.get("summary") or row.get("content") or "").strip()
        self.snippet = (raw_snip or row.get("ai_summary") or "")[:300]
        # 核验结果由 verify.verify_all() 回填
        self.trust = ""
        self.verified = False
        self.verify_reason = ""
        self.official_url = ""
        # 由小模型核查回填：范围级别 / 一句话要点 / 是否其实是解读文章
        self.scope = ""
        self.key_point = ""
        # 先用规则挡掉**明显不是禁飞通告**的 —— 解读文章、规划、
        # 竞赛通知、型号清单。这些占了法规库的大半，全丢给模型
        # 核查的话，核查配额（默认 20 条）根本不够，
        # 剩下的就全被当成禁飞通告堆进晨报。
        self.not_notice = _obviously_not_notice(row.get("title") or "")
        # 数据库里已标注的地区也算
        reg = (row.get("region") or "").strip()
        if reg and reg not in ("全国", "") and reg not in self.regions:
            self.regions.insert(0, reg)

        pub_year = None
        pub = (row.get("published_at") or "")[:4]
        if pub.isdigit():
            pub_year = int(pub)
        eff = (row.get("effective_at") or "")[:10]
        s, e, raw = extract_window(blob, pub_year)
        # 数据库里标注的 effective_at 优先
        if not s and eff:
            try:
                s = datetime.fromisoformat(eff).date()
                raw = eff
            except Exception:                  # noqa: BLE001
                pass
        self.start, self.end, self.raw_when = s, e, raw

        # 是否在用户辖区
        my = city or ""
        self.in_my_area = False
        if my:
            for r in self.regions:
                if my in r or r in my:
                    self.in_my_area = True
                    break
            if not self.in_my_area and province:
                for r in self.regions:
                    if r == province:
                        self.in_my_area = True
                        break
            # 深圳的区也算命中
            if not self.in_my_area and "深圳" in my:
                if any(r.startswith("深圳") for r in self.regions):
                    self.in_my_area = True
        # 全国性通告不是"在你辖区" —— 它只说明范围广，
        # 但用户最该被提醒的是本地禁飞，所以不能混为一谈。
        # 这里只在"全国真禁飞通告"时才给一个次高级别。
        self.nationwide = bool(not self.regions and reg == "全国")

        self.days_until = (self.start - today).days if self.start else None
        # "生效中"只在有明确时间窗时成立。只有起始日期、没有结束日期的，
        # 判定为"已生效"而不是"正在禁飞"——后者会误导飞手以为现在不能飞。
        self.active_now = bool(
            self.start and self.end and self.start <= today <= self.end)

    # ------------------------------------------------------------------
    @property
    def ended(self) -> bool:
        """禁飞期是否已经过去。"""
        if not self.end:
            return False
        return self.end < date.today()

    @property
    def severity(self) -> int:
        """紧迫度：数字越大越急。用于排序与强调级别。"""
        score = 0
        if self.ended:
            score -= 200                      # 已结束的排到最后
        if self.in_my_area:
            score += 100
        if self.active_now:
            score += 60                       # 眼下正在禁飞
        elif self.days_until is not None and 0 <= self.days_until <= 7:
            score += 50                       # 一周内开始
        elif self.days_until is not None and 0 <= self.days_until <= 30:
            score += 25
        if self.end and self.start:
            score += 15                       # 有明确时段更可信
        elif self.start:
            score += 5
        if self.nationwide:
            score -= 10                       # 全国性公告对个人的紧迫度低
        if self.verified or self.trust == "官方":
            score += 30                       # 官方源的优先展示
        if self.not_notice:
            score -= 300                      # 模型判定不是通告的沉底
        if self.scope == "单个地点":
            score -= 15                       # 局地管控不如全域急迫
        elif self.scope == "部分区域":
            score -= 8                        # 用户："也只是区域性的" —— 降但少降
        # 没有时间窗的旧闻只当参考
        if not self.start:
            score -= 20
        return score

    @property
    def level(self) -> str:
        """强调级别：critical / warn / info —— 决定晨报里的视觉分量。"""
        # 已结束的禁飞不再预警 —— 它对"今天能不能飞"没有指导意义，
        # 留在"重点"里只会稀释真正需要看的告警。
        if self.ended or self.not_notice:
            return "info"                       # 已过期，或模型判定不是通告
        # 未经核验的本地禁飞只能到 warn —— 二手转述可能是误读、旧闻或
        # 局地通告被放大成全市，不能以"重点"级别推给用户。
        verified_ok = self.verified or self.trust == "官方"
        if self.in_my_area and verified_ok and (
                self.active_now or
                (self.days_until is not None and 0 <= self.days_until <= 7)):
            # 区域性管控不配 critical —— 用户："7.20 开始的那个临时禁飞
            # 也只是区域性的啊"。只有模型核查确认"全域"才标红；
            # 部分区域/单个地点/不清楚 最高 warn，免得飞手以为全市禁飞。
            if self.scope in ("部分区域", "单个地点", "不清楚"):
                return "warn"
            if self.scope == "全域":
                # 模型判"全域"但正文里有四至/路段围合 —— 规则再兜一道：
                # 模型把区域性说成全市不是没发生过（用户实测抓到）。
                blob = " ".join(str(self.row.get(k) or "")
                                for k in ("title", "summary", "content"))
                from .llmcheck import _has_concrete_boundary
                if _has_concrete_boundary(blob):
                    return "warn"
                return "critical"
            return "critical"
        if self.in_my_area or self.active_now:
            return "warn"
        return "info"

    @property
    def when_text(self) -> str:
        if self.start and self.end:
            return f"{self.start.isoformat()} 至 {self.end.isoformat()}"
        if self.start:
            return f"{self.start.isoformat()} 起"
        return self.raw_when or "时间未标注"

    @property
    def area_text(self) -> str:
        return " / ".join(self.regions) if self.regions else "全国"


# 明显不是禁飞通告的标题特征 —— 这些是"说明性文件"，
# 不含任何时空限制，不该出现在禁飞预警里。
_NOT_NOTICE = re.compile(
    r"解读|政策|规划|评估报告|征求意见|答记者问|"
    r"竞赛|征稿|论文|表彰|名单|目录|统计|年报|"
    r"提案|建议|复文|会办意见|工作要点|"
    r"培训|会议|座谈|调研|考察")


def _obviously_not_notice(title: str) -> bool:
    """规则判据 —— 明显是说明性文件就直接排除。

    这一步省下的是模型核查配额：法规库里大半是解读文章，
    全让模型看的话配额瞬间用光，真正要紧的禁飞通告反而
    轮不到核查、全被当成禁飞堆进晨报。
    """
    return bool(_NOT_NOTICE.search(title or ""))


# 确认是禁飞通告的强信号 —— 标题里有这些基本跑不了
_IS_NOTICE = re.compile(
    r"禁飞|限飞|净空|临时管控|从严管控|空飘物|低慢小|"
    r"禁止飞行|限制飞行|飞行管控|管控的通告|禁飞的通告")


def looks_like_notice(title: str, body: str = "") -> bool:
    """标题里有强信号，就是禁飞通告。"""
    return bool(_IS_NOTICE.search(title or ""))


def scan_no_fly(rows: list[dict], city: str = "", province: str = "",
                today: date | None = None, fetcher=None,
                enrich_limit: int = 12, verify_limit: int = 12,
                llm=None, llm_limit: int = 20) -> list[NoFlyAlert]:
    """从一批条目里扫出临时禁飞预警，按紧迫度排序。

    fetcher 传入时：
      · 对"疑似本地禁飞但缺时间窗"的条目抓正文补全时段
      · 对本地预警核验来源，只把官方源/已定位官方原文的当成"重点"

    用户每天要多花几十秒等核验，但换来的是不会把自媒体误读当成
    真禁飞推给他 —— 这个交换是值得的。
    """
    out: list[NoFlyAlert] = []
    for r in rows:
        if not is_no_fly(r):
            continue
        try:
            out.append(NoFlyAlert(r, city, province, today))
        except Exception as e:                 # noqa: BLE001
            log.debug("禁飞解析失败 %s: %s", r.get("title"), e)

    # 先做来源核验 —— 官方源的 URL 会让下一步补全更有针对性
    if verify_limit > 0:
        try:
            from .verify import verify_all
            verify_all(out, fetcher=fetcher, max_checks=verify_limit)
        except Exception as e:                 # noqa: BLE001
            log.warning("来源核验失败（不影响禁飞扫描）: %s", e)

    # 正文补全：本地相关 + 没有完整时段的最值得补。
    # 官方源即使已有摘要也要补 —— 搜索摘要普遍只有 90 字左右，
    # 常把"即日起至11月23日"这类关键时段截掉，而政府网站可抓。
    if fetcher is not None and enrich_limit > 0:
        todo = [a for a in out
                if a.in_my_area and not (a.start and a.end)
                and not a.ended]
        # 官方源排最前 —— 它们既能抓到又最有价值；自媒体站抓了也白抓。
        todo.sort(key=lambda a: (0 if a.trust == "官方" else
                                 1 if a.trust == "媒体转述" else 2,
                                 -a.severity))
        todo = todo[:enrich_limit]
        if todo:
            # 关键：补全是"锦上添花"，绝不能因为它失败而让整个禁飞扫描
            # 甚至晨报报错。所有异常在此吞掉，超时直接放弃未完成的。
            try:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(
                        max_workers=min(6, len(todo))) as ex:
                    futs = [ex.submit(_enrich_alert, a, fetcher, today)
                            for a in todo]
                    done, pending = _cf.wait(futs, timeout=30)
                    for fu in done:
                        try:
                            fu.result()
                        except Exception as e:  # noqa: BLE001
                            log.debug("正文补全失败: %s", e)
                    if pending:
                        log.info("正文补全超时，放弃 %d 条", len(pending))
            except Exception as e:              # noqa: BLE001
                log.warning("正文补全整体失败（不影响禁飞扫描）: %s", e)

    # 小模型核查要点 —— 挑出"只是部分道路管控"却像全域的情形，
    # 并挡掉把政策解读误当通告的条目。用户接受首次多花几分钟。
    if llm is not None and llm_limit > 0:
        try:
            from .llmcheck import BriefChecker, apply_check
            ck = BriefChecker(llm)
            # 本地相关、未过期的优先核查（外地的不值得花算力）。
            # 排序：标题带强信号的排最前 —— 核查配额有限（默认 20），
            # 花在"诚信经营评价"这种标题上纯属浪费。
            todo = [a for a in out if a.in_my_area and not a.ended]
            todo.sort(key=lambda a: (0 if looks_like_notice(a.title) else 1,
                                     -a.severity))
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=2) as ex:
                futs = {ex.submit(ck.check, a, a.snippet): a
                        for a in todo[:llm_limit]}
                for fu in _cf.as_completed(futs):
                    a = futs[fu]
                    try:
                        apply_check(a, fu.result())
                    except Exception as e:     # noqa: BLE001
                        log.debug("核查回填失败: %s", e)
        except Exception as e:                 # noqa: BLE001
            log.warning("模型核查失败（不影响禁飞扫描）: %s", e)

    # 补全后重新核验一次 —— 抓到官方原文的媒体转述可以升级为可信
    if verify_limit > 0 and fetcher is not None:
        try:
            from .verify import verify_all
            verify_all(out, fetcher=None)      # 只重算等级，不再联网
        except Exception as e:                 # noqa: BLE001
            log.debug("复核失败: %s", e)

    # 出口过滤：确认不是通告的（规则挡掉 + 模型核查判定的）
    # 直接不返回 —— 这一步是关键。之前不过滤，解读文章、规划、
    # 竞赛通知全都被当成"禁飞预警"堆进晨报，用户看到的全是噪音。
    out = [a for a in out if not a.not_notice]
    out.sort(key=lambda a: -a.severity)
    return out


# 抓不动的站点直接跳过 —— 这些站点反爬严格，重试只会浪费时间
_SKIP_HOSTS = ("toutiao.com", "zhihu.com", "sohu.com/a/", "baidu.com",
               "360kuai.com", "haokan.", "weixin.qq.com", "douyin.com")


def _enrich_alert(a: "NoFlyAlert", fetcher, today: date,
                  timeout: float = 8.0) -> bool:
    """抓正文，补全禁飞时间窗与更精确的地区。返回是否补到时段。"""
    if not a.url:
        return False
    if any(h in a.url for h in _SKIP_HOSTS):
        return False
    try:
        html = fetcher.get_text(a.url)
    except Exception:                          # noqa: BLE001
        return False
    if not html:
        return False

    # 只留前 6000 字 —— 禁飞时段通常出现在正文开头
    text = _strip_html(html)[:6000]
    if not text:
        return False

    # 地区也要在正文里找一遍（标题常常不带地名）
    for r in extract_regions(text[:2000]):
        if r and r not in a.regions:
            a.regions.append(r)
    my = a.row.get("_city") or ""
    if my:
        a.in_my_area = any(my in r or r in my for r in a.regions) or a.in_my_area

    if not a.area:
        a.area = extract_area(text)

    # 官方原文的正文比搜索摘要详细得多 —— 用它替换掉 91 字的短摘要，
    # 晨报里就能给出"详细一点的概括"而不是一句话。
    body = _extract_body(text)
    if body and len(body) > len(a.snippet or ""):
        a.snippet = body[:400]

    s, e, raw = extract_window(text, a.start.year if a.start else None)
    if s:
        a.start, a.end, a.raw_when = s, e, raw
        a.days_until = (s - today).days
        a.active_now = bool(e and s <= today <= e)
        return True
    return False


def _extract_body(text: str, limit: int = 400) -> str:
    """从抓到的页面文本里取出真正的正文段落。

    跳过导航/"上一篇下一篇"/版权等噪声 —— 判据是句子里是否含
    实质信息（句子够长、含标点、不是纯链接列表）。
    """
    if not text:
        return ""
    noise = ("版权所有", "京ICP", "粤ICP", "网站地图", "联系我们",
             "上一篇", "下一篇", "打印本页", "关闭窗口", "扫一扫",
             "主办单位", "承办单位", "技术支持", "浏览次数",
             # 政府站的导航面包屑与栏目名 —— 抓正文时最容易混进来
             "当前位置", "首页 >", "首页>", "政务公开", "信息公开",
             "工作动态", "通知公告 >", "信息来源：", "信息提供日期",
             "分享到", "字体：", "打印", "返回顶部", "无障碍")
    # 含这些词的段落优先 —— 飞手真正关心的是时段、范围、依据
    key = ("至", "起", "禁止", "管控", "区域", "时段", "月", "日",
           "依据", "规定", "无人机", "航空器")

    picked: list[str] = []
    total = 0
    for seg in re.split(r"[。！？\n]", text):
        seg = seg.strip()
        if len(seg) < 14:                      # 太短的基本是导航/按钮
            continue
        if any(n in seg for n in noise):
            continue
        if seg.count("http") or seg.count("分享"):
            continue
        # 纯栏目名/标题重复的行：没有关键信息就跳过
        if not any(k in seg for k in key):
            continue
        picked.append(seg)
        total += len(seg)
        if total >= limit:
            break
    if not picked:
        return ""
    return "。".join(picked)[:limit]


_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


def _strip_html(html: str) -> str:
    """极简 HTML 转文本 —— 不引第三方库，够用即可。"""
    t = _TAG_RE.sub(" ", html)
    t = _ANY_TAG.sub(" ", t)
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    t = _WS.sub(" ", t)
    return re.sub(r"\n{2,}", "\n", t).strip()
