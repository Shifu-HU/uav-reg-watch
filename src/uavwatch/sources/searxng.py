"""SearXNG 聚合搜索源。

调用 SearXNG 的 JSON API: GET {base}/search?q=...&format=json
SearXNG 默认在 settings.yml 里禁用了 json 格式，需要开启:
    search:
      formats:
        - html
        - json
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from ..fetcher import Fetcher
from ..models import RawItem
from .base import BaseSource

log = logging.getLogger("uavwatch.searxng")

# 无人机相关性关键词(用于从搜索结果里筛掉无关广告/新闻)
UAV_KEYWORDS = [
    "无人机", "无人驾驶航空器", "无人航空器", "低空", "空域", "民航局",
    "禁飞", "净空", "实名登记", "操控员", "eVTOL", "飞行管理", "适航",
]


class SearxngSource(BaseSource):
    name = "searxng"
    display = "SearXNG"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        super().__init__(cfg, fetcher)
        self.base_url = (cfg.get("base_url") or "http://127.0.0.1:8080").rstrip("/")
        self.fallbacks = [u.rstrip("/") for u in (cfg.get("fallbacks") or [])]
        self.timeout = int(cfg.get("timeout", 25))
        self.queries = cfg.get("queries") or ["无人机 新规"]
        self.city = cfg.get("city") or ""
        self.province = cfg.get("province") or ""
        self.time_range = cfg.get("time_range", "")
        self.engines = cfg.get("engines", "")
        self.per_query = int(cfg.get("per_query", 30))

    # ------------------------------------------------------------------
    @property
    def endpoints(self) -> list[str]:
        return [self.base_url] + self.fallbacks

    def health(self) -> tuple[bool, str]:
        """探测 SearXNG 是否可用。"""
        last = "未知"
        for ep in self.endpoints:
            try:
                r = self.fetcher.client.get(
                    f"{ep}/search",
                    params={"q": "无人机", "format": "json", "language": "zh-CN"},
                    timeout=30,
                )
                if r.status_code == 200:
                    try:
                        r.json()
                        return True, f"{ep} 可用"
                    except Exception:  # noqa: BLE001
                        return False, f"{ep} 未开启 json 格式(需在 settings.yml 加 json)"
                return False, f"{ep} 返回 HTTP {r.status_code}"
            except Exception as e:  # noqa: BLE001
                last = str(e)
        return False, f"无法连接: {last}"

    # ------------------------------------------------------------------
    def fetch(self, since: str = "", full: bool = False) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        ep = self._pick_endpoint()
        if not ep:
            log.warning("SearXNG 不可用，跳过该源")
            return []

        tr = self.time_range
        if full:
            tr = "year"          # 首次全量: 拉一年
        queries = list(self.queries)
        if full:
            queries += [
                "无人驾驶航空器飞行管理暂行条例",
                "民用无人驾驶航空器实名制登记管理规定",
                "无人机 管理规定 全文",
                "低空飞行服务保障体系建设",
                "无人驾驶航空器 空域 管理办法",
                "无人机 违规 处罚 规定",
                "民航局 无人机 规范性文件",
                "无人机 适航 管理 规定",
                "无人机 运营 许可 办法",
                "无人驾驶航空器 标准",
            ]

        for q in queries:
            for it in self._search(ep, q, tr, since):
                if it.uid not in seen:
                    seen.add(it.uid)
                    items.append(it)

        # 临时禁飞专项检索 —— 这类通告有效期短、普通关键词容易漏，
        # 但对飞手恰恰最要紧，所以单独跑一轮、且不受 time_range 限制。
        try:
            nf = self.fetch_no_fly(ep, since)
            for it in nf:
                if it.uid not in seen:
                    seen.add(it.uid)
                    items.append(it)
            if nf:
                log.info("SearXNG: 临时禁飞专项命中 %d 条", len(nf))
        except Exception as e:                 # noqa: BLE001
            log.warning("临时禁飞检索失败: %s", e)

        log.info("SearXNG: 抓取到 %d 条", len(items))
        return items

    # ------------------------------------------------------------------
    def no_fly_queries(self) -> list[str]:
        """按所在地生成临时禁飞查询词。"""
        c = self.city or ""
        pv = self.province or ""
        qs: list[str] = []
        if c:
            qs += [
                f"{c} 无人机 临时禁飞 通告",
                f"{c} 禁飞 无人机 最新",
                f"{c} 低空 飞行 管控 通告",
                f"{c} 公安 无人机 禁飞",
            ]
        if pv and pv != c:
            qs += [
                f"{pv} 无人机 禁飞 通告",
                f"{pv} 低空 管控 通告",
            ]
        qs.append("无人机 临时禁飞 通告")
        return qs

    def fetch_no_fly(self, ep: str = "", since: str = "") -> list[RawItem]:
        """专项抓取临时禁飞/管控通告。

        与主检索的差别：
          1. 不带 time_range —— 禁飞通告常常是几年前的公告页仍有效，
             按时间过滤反而全部漏掉
          2. 放宽相关性判定 —— 只要标题含禁飞信号就收
        """
        ep = ep or self._pick_endpoint()
        if not ep:
            return []
        out: list[RawItem] = []
        seen: set[str] = set()
        for q in self.no_fly_queries():
            try:
                r = self.fetcher.client.get(
                    f"{ep}/search",
                    params={"q": q, "format": "json", "language": "zh-CN",
                            "safesearch": "0", "categories": "general"},
                    timeout=self.timeout)
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception as e:             # noqa: BLE001
                log.debug("禁飞查询失败 %s: %s", q, e)
                continue

            for res in (data.get("results") or [])[: self.per_query]:
                title = (res.get("title") or "").strip()
                url = (res.get("url") or "").strip()
                if not title or not url:
                    continue
                if not self._looks_no_fly(title):
                    continue
                item = RawItem(
                    title=title,
                    url=url,
                    source=self.name,
                    source_name=f"SearXNG({res.get('engine', '')})".rstrip("()"),
                    published_at=self._norm_date(res.get("publishedDate") or ""),
                    content=(res.get("content") or "").strip(),
                    channel="临时禁飞",
                )
                if item.uid not in seen:
                    seen.add(item.uid)
                    out.append(item)
        return out

    @staticmethod
    def _looks_no_fly(title: str) -> bool:
        """标题含禁飞/管控信号才收 —— 避免把政策解读灌进来。"""
        t = title.replace(" ", "")
        strong = ("禁飞", "限飞", "禁止飞行", "停飞", "临时管控", "低空管控",
                  "飞行管控", "净空")
        if any(w in t for w in strong):
            return True
        # 「XX期间加强低空飞行管控」这类
        if "管控" in t and ("低空" in t or "飞行" in t or "无人机" in t):
            return True
        return False

    # ------------------------------------------------------------------
    def _pick_endpoint(self) -> str:
        for ep in self.endpoints:
            try:
                r = self.fetcher.client.get(
                    f"{ep}/search",
                    params={"q": "无人机", "format": "json", "language": "zh-CN"},
                    timeout=30,
                )
                if r.status_code == 200:
                    r.json()
                    return ep
            except Exception:  # noqa: BLE001
                continue
        return ""

    # ------------------------------------------------------------------
    def _search(self, ep: str, q: str, time_range: str, since: str) -> list[RawItem]:
        params: dict[str, str] = {
            "q": q,
            "format": "json",
            "language": "zh-CN",
            "safesearch": "0",
            "categories": "general",
        }
        if time_range:
            params["time_range"] = time_range
        if self.engines:
            params["engines"] = self.engines

        out: list[RawItem] = []
        try:
            r = self.fetcher.client.get(f"{ep}/search", params=params, timeout=self.timeout)
            if r.status_code != 200:
                return out
            data = r.json()
        except Exception as e:  # noqa: BLE001
            log.debug("SearXNG 查询失败 %s: %s", q, e)
            return out

        for res in (data.get("results") or [])[: self.per_query]:
            title = (res.get("title") or "").strip()
            url = (res.get("url") or "").strip()
            if not title or not url:
                continue
            content = (res.get("content") or "").strip()
            if not self._is_relevant(title + " " + content):
                continue
            pub = self._norm_date(res.get("publishedDate") or "")
            if since and pub and pub < since[:10]:
                continue
            out.append(RawItem(
                title=title,
                url=url,
                source=self.name,
                source_name=f"SearXNG({res.get('engine', '')})".rstrip("()"),
                published_at=pub,
                content=content,
                channel="搜索",
            ))
        return out

    # ------------------------------------------------------------------
    @staticmethod
    def _is_relevant(text: str) -> bool:
        t = text.replace(" ", "")
        if any(k in t for k in UAV_KEYWORDS):
            return True
        return "民航" in t and "规定" in t

    @staticmethod
    def _norm_date(s: str) -> str:
        if not s:
            return ""
        s = s.strip()
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(s.replace("Z", "+0000") if "+" not in s and "Z" in s else s, fmt).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                continue
        return s[:10] if len(s) >= 10 else ""
