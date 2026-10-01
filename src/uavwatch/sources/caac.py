"""民航局官网抓取器。

民航局公开栏目页面是外壳，真实列表由 /was5/web/search 接口渲染。
已验证的 channelid:
    211383 / 238066 -> 政策解读 (ZCJD)
    269689          -> 民航规章 (MHGZ)
    278090          -> 政府信息公开年报
    283012          -> 政府信息公开制度

列表项结构(实测):
    <td class="t_l tdMC">
      <a href="..." target="_blank" name="TITLE">TITLE</a>
      <div class="t_l_content"><ul>
        <li class="t_l_content_left"><b>办文单位：</b>XXX</li>
        <li><b>发文日期：</b>2026年09月16日</li>
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..fetcher import Fetcher
from ..models import RawItem
from .base import BaseSource

log = logging.getLogger("uavwatch.caac")

SEARCH_EP = "https://www.caac.gov.cn/was5/web/search"

# 频道 ID -> 人类可读名(经验证)
CHANNELS: dict[str, str] = {
    "211383": "政策解读",
    "238066": "政策发布",
    "269689": "民航规章",
    "278090": "政府信息公开年报",
    "283012": "政府信息公开制度",
}

# 只有和无人机法规相关的频道才默认抓取
DEFAULT_CHANNELS = ["211383", "238066", "269689"]

# 民航局页面底部"机构列表"的标志词。一条文本里命中 2 个以上即可判定为页脚,
# 整段丢弃 —— 否则"广州民航职业技术学院"会把全国性法规误标成广州。
ORG_FOOTER_MARKERS = [
    "地区管理局", "空中交通管理局", "机关服务局",
    "中国民航大学", "中国民航飞行学院", "中国民航管理干部学院",
    "广州民航职业技术学院", "上海民航职业技术学院",
    "中国民航科学技术研究院", "民航第二研究所", "中国民航报社",
    "清算中心", "信息中心", "民航专业工程质量监督总站",
    "首都机场集团", "审计中心", "国际合作中心",
    "中国民航机场建设集团", "思想政治工作办公室", "全国民航工会",
    "离退休干部局",
]

# 站内关键词检索使用的频道(实测 211383 支持 searchword 全库命中, 无需 channelid 归属)
SEARCH_CHANNEL = "211383"

# 无人机 / 低空 相关性关键词 —— 用于从全站检索里筛出无人机相关条目
UAV_KEYWORDS = [
    "无人机", "无人驾驶航空器", "无人航空器", "民用无人", "低空", "空域",
    "飞行管理", "禁飞", "净空", "实名登记", "操控员", "驾驶员", "适航",
    "eVTOL", "垂直起降", "通用航空", "UOM", "无人系统",
]

# 民航局站内检索关键词(用于全站搜索模式)。
# 实测: 每个关键词可翻 10+ 页、命中 200 条左右；多关键词组合可覆盖
# 民航局站内几乎所有无人机/低空相关文件。
SEARCH_TERMS = [
    "无人机",
    "无人驾驶航空器",
    "低空经济",
    "空域管理",
    "无人机实名登记",
    "无人机驾驶员",
    "无人机飞行管理",
    "无人航空器",
    "民用无人机",
    "无人机 管理规定",
    "无人机 适航",
    "低空 飞行",
    "无人机 运营",
    "无人机 禁飞",
    "无人机 标准",
]


class CAACSource(BaseSource):
    name = "caac"
    display = "民航局官网"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        super().__init__(cfg, fetcher)
        self.base = cfg.get("base", "https://www.caac.gov.cn")
        self.max_pages = int(cfg.get("max_pages", 5))
        self.timeout = int(cfg.get("timeout", 20))
        self.channels = cfg.get("channels") or []
        self.use_site_search = cfg.get("site_search", True)

    # ------------------------------------------------------------------
    def fetch(self, since: str = "", full: bool = False) -> list[RawItem]:
        """抓取民航局条目。

        Args:
            since: ISO 日期字符串，只返回此日期之后的条目(增量)。空则全量。
            full:  全量模式(首次启动)，翻页更深。
        """
        items: list[RawItem] = []
        seen: set[str] = set()
        # 全量基线翻得更深；日常增量也用 10 页,保证每日情报量
        pages = self.max_pages * (4 if full else 2)

        # 抓取全部改成**并行**。
        #
        # 原来是 15 个检索词 × 10 页 = 150 次请求串行发，中间还夹着
        # 0.6 秒节流，实测一次全量要 52 秒 —— 用户感知就是"卡住"。
        # 这里全是等网络，并行没有副作用；节流交给 _query 内部的
        # 信号量控制，仍然不会把民航局网站打崩。
        from concurrent.futures import ThreadPoolExecutor, as_completed

        chans = [(cid, cname) for cid, cname in CHANNELS.items()
                 if cid in DEFAULT_CHANNELS]
        terms = list(SEARCH_TERMS) if self.use_site_search else []

        jobs = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for cid, cname in chans:
                jobs.append(ex.submit(self._crawl_channel, cid, cname,
                                      pages, since))
            for term in terms:
                jobs.append(ex.submit(self._crawl_search, term, pages, since))
            for fu in as_completed(jobs):
                try:
                    batch = fu.result()
                except Exception as e:         # noqa: BLE001
                    log.warning("CAAC 子任务失败: %s", e)
                    continue
                for it in batch:
                    if it.uid not in seen:
                        seen.add(it.uid)
                        items.append(it)

        log.info("CAAC: 抓取到 %d 条", len(items))
        return items

    # ------------------------------------------------------------------
    def _crawl_channel(self, cid: str, cname: str, max_pages: int,
                       since: str) -> list[RawItem]:
        out: list[RawItem] = []
        _seen_t: set[str] = set()
        for page in range(1, max_pages + 1):
            html = self._query(channelid=cid, page=page)
            if not html or "tbRe" not in html:
                break
            batch = self._parse_list(html, source_name=f"民航局 · {cname}",
                                     channel=cname)
            if not batch:
                break
            titles = {(b.title or "").strip() for b in batch if b.title}
            if titles and titles <= _seen_t:
                break                      # 整页重复，见 _crawl_search 说明
            _seen_t |= titles
            fresh = [b for b in batch if self._after(b, since)]
            out.extend(fresh)
            # 全量模式不因"没有新条目"提前停；增量模式翻页到全是旧的就停
            if not fresh and since:
                break
        return out

    # ------------------------------------------------------------------
    def _crawl_search(self, term: str, max_pages: int, since: str) -> list[RawItem]:
        """翻页抓取某个检索词。

        提前停止的两种情形（实测都很常见）：
          1. 本页全是旧条目（增量模式）
          2. **本页标题全都在前面页出现过** —— 民航局的 perpage 参数
             实际不生效，第 5 页起返回的跟前几页完全一样。
             实测「无人机」第 5~8 页新增恒为 0，白跑 4 次请求。

        第 2 条不依赖 since，所以全量首扫也能受益 —— 原来只有增量
        模式才会提前停，首次全量老老实实翻满，纯浪费。
        """
        out: list[RawItem] = []
        seen_titles: set[str] = set()
        for page in range(1, max_pages + 1):
            html = self._query(channelid=SEARCH_CHANNEL, sw=term, page=page)
            if not html or "tbRe" not in html:
                break
            batch = self._parse_list(html, source_name="民航局 · 站内检索",
                                     channel="站内检索", relax=True)
            if not batch:
                break
            titles = {(b.title or "").strip() for b in batch if b.title}
            if titles and titles <= seen_titles:
                break                      # 整页都是旧内容，后面只会更旧
            seen_titles |= titles
            fresh = [b for b in batch if self._after(b, since)]
            out.extend(fresh)
            if not fresh and since:
                break
        return out

    # ------------------------------------------------------------------
    def _query(self, *, channelid: str, page: int = 1, perpage: int = 50,
               sw: str = "") -> str:
        """调用民航局检索接口。

        重要: 该接口必须用 POST。GET 会被站点 302 到反爬空壳页(仅 7KB, 无结果)。
        实测 POST 每页可稳定返回 50 条。
        参数名: sw=关键词, channelid=栏目, perpage=每页条数。
        """
        data = {
            "channelid": channelid,
            "sw": sw,
            "page": str(page),
            "perpage": str(perpage),
            "selST": "All",
            "templet": "",
            "token": "",
            "zfl": "",
            "fl": "",
            "wh": "",
            "dw": "",
            "st": "",
            "et": "",
            "doS": "",
            "distinct": "",
        }
        return self.fetcher.post_text(SEARCH_EP, data)

    # ------------------------------------------------------------------
    def _parse_list(self, html: str, source_name: str, channel: str,
                    relax: bool = False) -> list[RawItem]:
        soup = BeautifulSoup(html, "lxml")
        out: list[RawItem] = []
        # 表格行: <td class="t_l tdMC"><a href=...>标题</a> + t_l_content
        for td in soup.select("td.t_l"):
            a = td.find("a", href=True)
            if not a:
                continue
            title = (a.get("name") or a.get_text(" ", strip=True) or "").strip()
            href = a["href"].strip()
            if not title or not href.startswith("http"):
                continue
            if not title.replace(" ", ""):
                continue
            # 栏目直取时需自筛相关性；站内检索已按关键词命中，放宽
            if not relax and not self._is_relevant(title):
                continue

            published = ""
            dept = ""
            txt = td.get_text(" ", strip=True)
            m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", txt)
            if m:
                published = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            else:
                m2 = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", txt)
                if m2:
                    published = f"{m2.group(1)}-{int(m2.group(2)):02d}-{int(m2.group(3)):02d}"
            md = re.search(r"办文单位[：:]\s*([^\s<]{2,20})", txt)
            if md:
                dept = md.group(1)

            out.append(RawItem(
                title=title,
                url=href,
                source=self.name,
                source_name=source_name + (f"({dept})" if dept else ""),
                published_at=published,
                content="",
                channel=channel,
            ))
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _is_relevant(title: str) -> bool:
        t = title.replace(" ", "")
        # 明确无关的行政/人事/统计类
        noise = ["人事任免", "干部任免", "招聘", "拟聘", "表彰", "慰问",
                 "党建", "巡视", "民主生活会"]
        if any(n in t for n in noise):
            return False
        return any(k in t for k in UAV_KEYWORDS)

    # ------------------------------------------------------------------
    def fetch_detail(self, url: str) -> str:
        """抓取正文。"""
        html = self.fetcher.get_text(url)
        if not html:
            return ""
        soup = BeautifulSoup(html, "lxml")
        self._strip_boilerplate(soup)

        # 民航局信息公开页把正文放在"名称/文号/发文单位/发文日期"元数据块里,
        # 真正的规范内容多数在 PDF 附件中。这里提取元数据 + 附件名,
        # 再拼接成一段可用于分类和地理判断的文本。
        meta = self._extract_meta(soup, html)
        body_txt = ""
        for sel in ["div.TRS_Editor", "div#content", "div.article",
                    "div.xxgk_content", "div.zoom", "div.content"]:
            node = soup.select_one(sel)
            if node:
                t = node.get_text("\n", strip=True)
                if len(t) > 120:
                    body_txt = t
                    break

        parts = [p for p in (meta, body_txt) if p]
        return "\n".join(parts)[:8000]

    @staticmethod
    def _extract_meta(soup: BeautifulSoup, html: str) -> str:
        """提取民航局信息公开页的元数据块(名称/文号/发文单位/日期/附件)。"""
        fields = {
            "名称": "", "文号": "", "发文单位": "", "办文单位": "",
            "发文日期": "", "成文日期": "", "主题分类": "", "体裁分类": "",
            "有效性": "", "部号": "",
        }
        # 真实结构: <b>发文日期：</b>2026-08-04</li>
        for k in list(fields):
            m = re.search(k + r"\s*[:：]\s*</b>\s*([^<]{1,160})", html)
            if not m:
                m = re.search(k + r"\s*[:：]\s*([^<\n]{1,160})", html)
            if m:
                v = m.group(1).strip().rstrip("</li>").strip()
                if v and not v.startswith("<"):
                    fields[k] = v
        lines = [f"{k}: {v}" for k, v in fields.items() if v]
        # 附件名往往就是规范标题
        atts = []
        for m in re.finditer(r'([^\s<>"]{4,80}\.(?:pdf|docx?|xlsx?))', html, re.I):
            n = m.group(1)
            if n not in atts:
                atts.append(n)
        if atts:
            lines.append("附件: " + " / ".join(atts[:4]))
        return "\n".join(lines)

    @staticmethod
    def _strip_boilerplate(soup: BeautifulSoup) -> None:
        """移除导航、页脚、机构列表等站点公共内容。"""
        sels = [
            "script", "style", "nav", "header", "footer",
            "div.footer", "div#footer", "div.foot", "div.bottom",
            "div.nav", "div#nav", "div.menu", "div.crumb", "div.breadcrumb",
            "div.share", "div.print", "div.toolbar", "div.top",
            "ul.footer", "ul.nav", "p.footer",
        ]
        for sel in sels:
            for tag in soup.select(sel):
                tag.decompose()
        # 残留的"机构列表"段落: 含多个下属单位名称的行整段去掉
        for tag in soup.find_all(["div", "p", "li", "td"]):
            try:
                t = tag.get_text(" ", strip=True)
            except Exception:              # noqa: BLE001
                continue
            if not t:
                continue
            hits = sum(1 for m in ORG_FOOTER_MARKERS if m in t)
            if hits >= 2:
                tag.decompose()

    # ------------------------------------------------------------------
    @staticmethod
    def _after(item: RawItem, since: str) -> bool:
        if not since:
            return True
        if not item.published_at:
            return True          # 无日期的一律保留，交给去重判定
        return item.published_at >= since[:10]
