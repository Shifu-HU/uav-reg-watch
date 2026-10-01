"""每日调度: 定时任务 + 补跑判断 + 每日 200 条配额保障。"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, date, timedelta
from typing import Callable

log = logging.getLogger("uavwatch.scheduler")


class DailyScheduler:
    """轻量调度器(不依赖 APScheduler，减少体积，便于将来移植手机端)。"""

    def __init__(self, cfg, store, run_fn: Callable[[str], dict],
                 on_event: Callable[[str, dict], None] | None = None):
        self.cfg = cfg
        self.store = store
        self.run_fn = run_fn
        self.on_event = on_event or (lambda e, d: None)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    @property
    def next_run_at(self) -> datetime:
        hh, mm = (self.cfg.schedule.daily_time or "07:30").split(":")
        now = datetime.now()
        target = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    def seconds_until_next(self) -> float:
        return max(5.0, (self.next_run_at - datetime.now()).total_seconds())

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="uavwatch-sched", daemon=True)
        self._thread.start()
        log.info("调度器已启动，下次运行 %s", self.next_run_at.strftime("%Y-%m-%d %H:%M"))

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        # 启动时先做一次补跑判断
        try:
            if not self.store.bootstrap_done:
                self._execute("bootstrap")
            elif self._needs_catchup():
                self._execute("daily")
        except Exception:
            log.exception("启动补跑失败")

        while not self._stop.is_set():
            wait = self.seconds_until_next()
            if self._stop.wait(min(wait, 60)):
                break
            if self.seconds_until_next() > 2:
                continue
            self._execute("daily")
            # 跑完再睡，避免同一分钟内重复触发
            self._stop.wait(90)

    def _needs_catchup(self) -> bool:
        """判断是否需要补搜。

        触发条件(满足任一):
          1. 从未搜索过
          2. 距上次搜索超过 catchup_after_hours(默认 8 小时)
          3. 今天还没搜过 —— 这是关键: 软件 7:30 可能没开机,
             用户当天第一次打开时就该补上,而不是干等到明早。

        普通飞手不会 24 小时开着软件,所以"当天首次打开必须搜一遍"
        比"严格按 7:30 执行"更符合实际。
        """
        last = self.store.last_search_at
        if not last:
            return True
        try:
            dt = datetime.fromisoformat(last)
        except Exception:                 # noqa: BLE001
            return True
        # 今天还没搜过 -> 补搜(覆盖"错过 7:30"的场景)
        if dt.date() < date.today():
            return True
        return (datetime.now() - dt) > timedelta(hours=self.cfg.schedule.catchup_after_hours)

    # ------------------------------------------------------------------
    def on_app_open(self) -> tuple[bool, str]:
        """用户打开软件时调用。返回 (是否已触发搜索, 说明文字)。

        设计意图: 软件不一定 7:30 在线, 所以开屏就是一次"补课"机会。
        这里同步判断、异步执行, 不阻塞界面启动。
        """
        try:
            if not self.store.bootstrap_done:
                self.run_now("bootstrap")
                return True, "首次使用，正在建立历史基线（约 5-10 分钟）"
            if self._needs_catchup():
                last = self.store.last_search_at or "从未"
                self.run_now("daily")
                return True, f"距上次搜索（{last[:16]}）已有新时段，正在补搜今日动态"
            return False, "今日已搜索过，数据是最新的"
        except Exception as e:            # noqa: BLE001
            log.exception("开屏补搜判断失败")
            return False, f"检查失败: {e}"

    # ------------------------------------------------------------------
    def _execute(self, kind: str) -> dict:
        if self._running:
            log.info("已有任务在跑，跳过本次 %s", kind)
            return {}
        self._running = True
        try:
            self.on_event("run_start", {"kind": kind})
            res = self.run_fn(kind)
            self.on_event("run_done", res)
            # 每日配额保障: 不足则再补一轮
            self._ensure_quota()
            # 跑完刷新晨报 —— 用户打开软件看到的就是最新一版
            self._build_brief()
            return res
        except Exception as e:
            log.exception("任务失败")
            self.on_event("run_error", {"kind": kind, "error": str(e)})
            return {}
        finally:
            self._running = False

    def run_now(self, kind: str = "manual") -> None:
        threading.Thread(target=self._execute, args=(kind,), daemon=True).start()

    # ------------------------------------------------------------------
    def _build_brief(self) -> None:
        """生成当日晨报。失败不影响主流程。"""
        try:
            from .morning import MorningBrief
            brief = MorningBrief(self.cfg, self.store, self.cfg.data_dir)
            md, html, txt, data = brief.build()
            log.info("晨报已更新: %s", html)
            self.on_event("brief_ready", {
                "html": str(html), "md": str(md), "txt": str(txt),
                "count": len(data["all"]), "headline": brief.headline(),
            })
        except Exception:                 # noqa: BLE001
            log.exception("生成晨报失败")

    # ------------------------------------------------------------------
    def _ensure_quota(self) -> None:
        """保证当日推送量达到 min_daily_items。不足则再跑一轮抓取。"""
        target = int(self.cfg.schedule.min_daily_items)
        today = date.today().isoformat()
        have = self.store.stats().get("today_new", 0)
        if have >= target:
            return
        log.info("今日 %d/%d，尝试补足配额", have, target)
        for i in range(2):
            if self.store.stats().get("today_new", 0) >= target:
                return
            try:
                self.run_fn("daily")
            except Exception:
                log.exception("配额补充失败")
                return
            if date.today().isoformat() != today:
                return
