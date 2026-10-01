"""任务编排: 首次全量基线 + 每日增量搜索 + AI 处理 + 地理过滤。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from .dedup import Deduper
from .fetcher import Fetcher
from .geo import GeoFilter
from .llm import LocalLLM
from .models import RawItem, RegItem
from .sources import BingSource, CAACSource, LocalSource, SearxngSource
from .storage import Store

log = logging.getLogger("uavwatch.pipeline")


class Pipeline:
    """把源 -> 去重 -> 地理过滤 -> AI -> 存储 串起来。"""

    def __init__(self, cfg, store: Store, progress: Callable[[str, int, int], None] | None = None):
        self.cfg = cfg
        self.store = store
        self.progress = progress
        self.fetcher = Fetcher(delay=cfg.schedule.request_delay)
        self.geo = GeoFilter(
            city=cfg.location.city,
            province=cfg.location.province,
            keep_national=cfg.location.keep_national,
            also_keep=cfg.location.also_keep,
        )
        self.deduper = Deduper(cfg.dedup.title_similarity)
        self.llm = LocalLLM(cfg.llm)

    def _emit(self, stage: str, cur: int = 0, total: int = 0) -> None:
        if self.progress:
            try:
                self.progress(stage, cur, total)
            except Exception:
                pass
        log.info("%s %s/%s", stage, cur, total)

    # ------------------------------------------------------------------
    def build_sources(self) -> list:
        s = self.cfg.sources
        out = []
        if s.get("caac", {}).get("enabled", True):
            out.append(CAACSource(s.get("caac", {}), self.fetcher))
        if s.get("searxng", {}).get("enabled", True):
            sc = dict(s.get("searxng", {}))
            # 把所在地传给搜索源 —— 用于生成"本地临时禁飞"专项查询
            sc.setdefault("city", getattr(self.cfg.location, "city", "") or "")
            sc.setdefault("province", getattr(self.cfg.location, "province", "") or "")
            out.append(SearxngSource(sc, self.fetcher))
        if s.get("bing", {}).get("enabled", True):
            bc = dict(s.get("bing", {}))
            # 查询串里的 {city}/{province} 需要这两个值
            bc.setdefault("city", getattr(self.cfg.location, "city", "") or "")
            bc.setdefault("province",
                          getattr(self.cfg.location, "province", "") or "")
            out.append(BingSource(bc, self.fetcher))
        # 地方公安 / 政府通告 —— 用户要求"必须确保是用户地区的"，
        # 所以 city/province 从 location 注入，不在源里写死。
        if s.get("local", {}).get("enabled", True):
            lc = dict(s.get("local", {}))
            lc.setdefault("city", getattr(self.cfg.location, "city", "") or "")
            lc.setdefault("province",
                          getattr(self.cfg.location, "province", "") or "")
            out.append(LocalSource(lc, self.fetcher))
        return out

    # ------------------------------------------------------------------
    def collect(self, since: str = "", full: bool = False) -> list[RawItem]:
        raw: list[RawItem] = []
        sources = self.build_sources()
        for i, src in enumerate(sources, 1):
            self._emit(f"正在抓取: {src.display}", i, len(sources))
            try:
                got = src.fetch(since=since, full=full)
            except Exception as e:
                log.warning("源 %s 抓取失败: %s", src.name, e)
                continue
            raw.extend(got)
            self._emit(f"{src.display} 完成", i, len(sources))
        log.info("共抓取原始条目 %d 条", len(raw))
        return raw

    # ------------------------------------------------------------------
    def to_regitems(self, raw: list[RawItem]) -> list[RegItem]:
        out = []
        for r in raw:
            out.append(RegItem(
                uid=r.uid, title=r.title, url=r.url, source=r.source,
                source_name=r.source_name, published_at=r.published_at,
                content=r.content, content_hash=r.content_hash,
            ))
        return out

    # ------------------------------------------------------------------
    def filter_new(self, items: list[RegItem]) -> list[RegItem]:
        """去掉库里已存在的(URL 命中 / 正文指纹命中 / 标题高度相似)。"""
        fresh: list[RegItem] = []
        for it in items:
            if self.store.exists_uid(it.uid):
                continue
            if it.content_hash and self.store.exists_hash(it.content_hash):
                continue
            fresh.append(it)
        # 与库内已有标题做相似度去重(只取近期标题，控制开销)
        known = [r["title"] for r in self.store.query(limit=3000, only_local=False)]
        fresh = self.deduper.dedup(fresh, known)
        fresh = self.limit_third_party(fresh)
        return fresh

    # ------------------------------------------------------------------
    def limit_third_party(self, items: list[RegItem]) -> list[RegItem]:
        """限制第三方来源占比，保证官方为主。

        用户要求：以民航局官网为主，第三方消息 10% 左右即可。
        规则：
          · 官方源（.gov.cn / 民航局）全部保留 —— 它们是权威依据
          · 第三方按"每 9 条官方配 1 条第三方"的比例截断
          · 官方不足时放宽，避免第三方被误杀导致抓取量骤降

        注意：这里只影响"入库的新条目"，不删库中原有数据。
        """
        try:
            from .verify import classify_trust, TRUST_OFFICIAL
        except Exception:                      # noqa: BLE001
            return items
        try:
            ratio = float(self.cfg.crawler.get("third_party_ratio", 0.10))
        except Exception:                      # noqa: BLE001
            ratio = 0.20
        if ratio >= 1.0 or not items:
            return items

        official, third = [], []
        for it in items:
            if classify_trust(it.url) == TRUST_OFFICIAL:
                official.append(it)
            else:
                third.append(it)

        if not third:
            return items
        # 官方 n 条 -> 第三方最多 n*ratio/(1-ratio) 条
        if ratio <= 0:
            allow = 0
        else:
            allow = int(len(official) * ratio / (1.0 - ratio))
        # 官方很少时（比如刚启动只抓到几条）别把第三方砍光，否则首次
        # 全量搜索会几乎没结果。但**只在官方不足时才兜底** ——
        # 否则官方充足时也会被托到 15 条，比例压不到 10%。
        if len(official) < 15:
            allow = max(allow, min(len(third), 15))

        if len(third) > allow:
            log.info("第三方来源限流: %d -> %d（官方 %d 条）",
                     len(third), allow, len(official))
            third = third[:allow]
        return official + third

    # ------------------------------------------------------------------
    def enrich(self, items: list[RegItem]) -> list[RegItem]:
        """抓正文 + AI 分析。"""
        self._emit("正在读取正文", 0, len(items))
        for i, it in enumerate(items, 1):
            if not it.content or len(it.content) < 80:
                try:
                    body = self._detail_for(it)
                    if body:
                        it.content = body
                        it.fetch_excerpt = body[:400]
                except Exception:
                    pass
            if i % 10 == 0:
                self._emit("正在读取正文", i, len(items))

        self._emit("本地模型分析中", 0, len(items))
        flushed = [0]

        def on_progress(cur: int, total: int, it: RegItem) -> None:
            self._emit("本地模型分析中", cur, total)
            # 每 10 条落盘一次: 进度查看器与界面都能实时看到增长
            if cur - flushed[0] >= 10:
                self._flush(items[flushed[0]:cur])
                flushed[0] = cur

        self.llm.analyze_batch(items, progress=on_progress)
        if flushed[0] < len(items):
            self._flush(items[flushed[0]:])
        return items

    def _flush(self, batch: list[RegItem]) -> None:
        """把已完成分析的一批条目先写入库(地理过滤后)。"""
        if not batch:
            return
        kept, dropped = self.apply_geo(batch)
        try:
            self.store.upsert_items(kept)
            if dropped:
                self.store.upsert_items(dropped)
        except Exception as e:            # noqa: BLE001
            log.debug("中途落盘失败: %s", e)

    def _detail_for(self, it: RegItem) -> str:
        for src in self.build_sources():
            if src.name == it.source:
                return src.fetch_detail(it.url)
        return ""

    # ------------------------------------------------------------------
    def apply_geo(self, items: list[RegItem]) -> tuple[list[RegItem], list[RegItem]]:
        """地理过滤: 非本地的剔除。用 AI 给出的 region 做二次校验。"""
        kept: list[RegItem] = []
        dropped: list[RegItem] = []
        for it in items:
            res = self.geo.classify(it.title, it.content or "",
                                    it.source_name or "", it.url or "")
            # 模型认为是非全国性且落在本地白名单里 -> 尊重模型判断
            ai_region = (it.region or "").strip()
            if not res.is_local and ai_region and ai_region in self.geo.local_ok:
                res.is_local = True
                res.region = ai_region
                res.reason = f"模型判定属于 {ai_region}"
            it.region = res.region
            it.region_scope = res.scope
            it.is_local = res.is_local
            (kept if res.is_local else dropped).append(it)
        return kept, dropped

    # ------------------------------------------------------------------
    def run(self, kind: str = "daily") -> dict:
        """执行一轮完整流程。kind: bootstrap / daily / manual"""
        full = kind == "bootstrap"
        run_id = self.store.start_run(kind)
        started = datetime.now()
        since = "" if full else self.store.last_search_at

        try:
            # 1. 抓取
            raw = self.collect(since=since, full=full)
            fetched = len(raw)

            # 2. 转模型 + 去重
            self._emit("正在去重", 0, fetched)
            cand = self.to_regitems(raw)
            fresh = self.filter_new(cand)
            log.info("新条目 %d 条 (原始 %d)", len(fresh), fetched)

            # 3. 正文 + AI
            if fresh:
                fresh = self.enrich(fresh)

            # 4. 地理过滤
            kept, dropped = self.apply_geo(fresh)

            # 5. 入库
            # 用入库前后的总条数差来算"真正新增" —— upsert 返回的是受影响
            # 行数(含更新已有记录)，直接当新增数会偏大。
            before_total = self.store.stats()["total"]
            self.store.upsert_items(kept)
            # 被剔除的也入库(标记 is_local=0)，便于用户在界面查看"已过滤"
            if dropped:
                self.store.upsert_items(dropped)
            inserted = max(0, self.store.stats()["total"] - before_total)
            log.info("入库: 候选 %d 条(保留 %d + 外地 %d)，实际新增 %d 条",
                     len(fresh), len(kept), len(dropped), inserted)

            # 6. 哨兵
            self.store.touch_last_search()
            if full:
                self.store.mark_bootstrap_done(inserted)

            # 7. 情报池: 每天真正的新规通常远少于 200 条,
            #    因此每日推送 = 当日新增 + 历史未读重要条目, 保证"每日不少于 200 条"。
            quota = self.cfg.schedule.min_daily_items
            pool = self.store.unread_pool(limit=max(0, quota - inserted))
            today_total = inserted + len(pool)

            elapsed = (datetime.now() - started).total_seconds()
            self.store.finish_run(
                run_id, fetched=fetched, new_items=inserted,
                kept_local=len(kept), filtered=len(dropped),
                status="ok", note=f"耗时 {elapsed:.0f}s",
            )
            self._emit("完成", 1, 1)
            log.info("今日可推送 %d 条 (新增 %d + 情报池 %d), 目标 %d",
                     today_total, inserted, len(pool), quota)

            return {
                "kind": kind, "fetched": fetched, "new": inserted,
                "kept_local": len(kept), "filtered": len(dropped),
                "pool": len(pool), "today_total": today_total, "quota": quota,
                "quota_met": today_total >= quota,
                "elapsed": elapsed, "run_id": run_id,
            }
        except Exception as e:
            log.exception("运行失败")
            self.store.finish_run(run_id, status="error", note=str(e)[:300])
            raise
        finally:
            self.fetcher.close()

    # ------------------------------------------------------------------
    def needs_catchup(self) -> bool:
        """距上次搜索超过阈值则需补跑。"""
        last = self.store.last_search_at
        if not last:
            return True
        try:
            dt = datetime.fromisoformat(last)
        except Exception:
            return True
        return (datetime.now() - dt) > timedelta(hours=self.cfg.schedule.catchup_after_hours)
