"""Bing 网页搜索辅助源。

直接抓取 cn.bing.com 结果页(HTML)，解析 b_algo 结果块。
不需要 API key，作为 SearXNG 与民航局之外的补充。
"""

from __future__ import annotations

import base64
import logging
import re
from urllib.parse import parse_qs, quote_plus, urlparse

from bs4 import BeautifulSoup

from ..fetcher import Fetcher
from ..models import RawItem
from .base import BaseSource

log = logging.getLogger("uavwatch.bing")

UAV_KEYWORDS = [
    "无人机", "无人驾驶航空器", "无人航空器", "低空", "空域", "民航局",
    "禁飞", "净空", "实名登记", "操控员", "eVTOL", "飞行管理", "适航",
]


class BingSource(BaseSource):
    name = "bing"
    display = "Bing"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        super().__init__(cfg, fetcher)
        self.queries = cfg.get("queries") or ["无人机 新规"]
        self.max_pages = int(cfg.get("max_pages", 3))
        self.timeout = int(cfg.get("timeout", 20))
        self.market = cfg.get("market", "cn.bing.com")
        # 地区：查询串里的 {city} / {province} 会被替换。
        # 这样 config 里就不用写死"深圳"—— 换城市改 location 即可，
        # 别的用户拿去也能直接用（用户要求"不止我"）。
        self.city = (cfg.get("city") or "").strip()
        self.province = (cfg.get("province") or "").strip()

    def _expand(self, q: str) -> str:
        """把查询串里的地区占位符换成实际值。

        city 为空时整段连带前面的空格去掉，避免搜出
        " 无人机 禁飞 site:.gov.cn" 这种带空站点的怪查询。
        """
        q = q.replace("{city}", self.city).replace("{province}", self.province)
        return re.sub(r"\s+", " ", q).strip()

    # ------------------------------------------------------------------
    def fetch(self, since: str = "", full: bool = False) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        queries = [self._expand(q) for q in self.queries]
        pages = self.max_pages * (2 if full else 1)
        if full:
            queries += [
                "无人驾驶航空器飞行管理暂行条例 全文",
                "民用无人驾驶航空器实名制登记管理规定",
                "无人机 空域 管理办法 民航局",
                "无人机 驾驶员 管理规定",
                "低空经济 政策 2026",
            ]

        for q in queries:
            for page in range(0, pages):
                for it in self._search_page(q, page * 10, since):
                    if it.uid not in seen:
                        seen.add(it.uid)
                        items.append(it)

        log.info("Bing: 抓取到 %d 条", len(items))
        return items

    # ------------------------------------------------------------------
    def _search_page(self, q: str, first: int, since: str) -> list[RawItem]:
        url = f"https://{self.market}/search?q={quote_plus(q)}&first={first}&setlang=zh-CN&ensearch=0"
        html = self.fetcher.get_text(url)
        if not html:
            return []
        return self._parse(html, q, since)

    # ------------------------------------------------------------------
    def _parse(self, html: str, query: str, since: str) -> list[RawItem]:
        soup = BeautifulSoup(html, "lxml")
        out: list[RawItem] = []
        for li in soup.select("li.b_algo"):
            # 优先取标题锚点: Bing 的 h2 > a 才是真正的结果标题,
            # 首个子 <a> 往往只包着域名,会把标题抓成 "zhihu.com" 这种垃圾值。
            a = li.select_one("h2 a[href]") or li.select_one("a[href]")
            if not a:
                continue
            title = a.get_text(" ", strip=True)
            # 标题是裸域名/过短 -> 换成 li 的 aria-label 或跳过
            if not title or len(title) < 6 or self._looks_like_domain(title):
                aria = li.get("aria-label", "")
                title = aria.strip() if aria and len(aria) > 6 else ""
            if not title or self._looks_like_domain(title):
                continue
            href = self._unwrap(a.get("href", ""))
            if not href.startswith("http"):
                continue
            # 过滤掉只有目录的导航型链接(如 news.qq.com › rain)
            if self._looks_like_topic_path(title, href):
                continue
            snippet_node = li.select_one("p, div.b_caption p")
            snippet = snippet_node.get_text(" ", strip=True) if snippet_node else ""
            if not self._is_relevant(title + " " + snippet):
                continue
            out.append(RawItem(
                title=title,
                url=href,
                source=self.name,
                source_name="Bing",
                published_at="",
                content=snippet,
                channel="搜索",
            ))
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _looks_like_domain(t: str) -> bool:
        """判断文本是否只是域名(如 zhihu.com / news.qq.com)。"""
        s = t.strip().lower()
        if " " in s:
            return False
        if "/" in s or "›" in s or "»" in s:
            return True
        return bool(re.fullmatch(r"[a-z0-9][a-z0-9\-.]*\.[a-z]{2,}", s))

    @staticmethod
    def _looks_like_topic_path(title: str, url: str) -> bool:
        """Bing 的聚合页/栏目页没有标题，跳过。"""
        if "›" in title or "»" in title:
            return True
        if re.search(r"/(topic|tags?|search|special)/", url):
            return True
        return False

    # ------------------------------------------------------------------
    @staticmethod
    def _unwrap(url: str) -> str:
        """Bing 会把结果 URL 包成 /ck/a?...&u=a1<base64>。解出真实地址。"""
        if "bing.com/ck/a" not in url:
            return url
        try:
            qs = parse_qs(urlparse(url).query)
            u = qs.get("u", [""])[0]
            if u.startswith("a1"):
                pad = "=" * (-len(u[2:]) % 4)
                raw = base64.urlsafe_b64decode(u[2:] + pad).decode("utf-8", "replace")
                return raw
        except Exception:  # noqa: BLE001
            pass
        return url

    # ------------------------------------------------------------------
    @staticmethod
    def _is_relevant(text: str) -> bool:
        t = text.replace(" ", "")
        return any(k in t for k in UAV_KEYWORDS)
