"""终端实时进度查看器 —— 无需图形界面即可观察软件运行状态。

用法:
    python -m uavwatch.tui            # 实时刷新(默认每秒)
    python -m uavwatch.tui -i 2       # 每 2 秒刷新
    python -m uavwatch.tui --once     # 只打印一屏后退出
    python -m uavwatch.tui --watch    # 边刷新边跟踪日志尾部
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from .config import load_config
from .storage import Store

# ---------------------------------------------------------------- 颜色 ----
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    GREY = "\033[90m"

    @classmethod
    def off(cls) -> None:
        for k in list(vars(cls)):
            if k.isupper():
                setattr(cls, k, "")


def _enable_ansi() -> None:
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            C.off()
    if not sys.stdout.isatty():
        C.off()


def w() -> int:
    return max(60, min(shutil.get_terminal_size((100, 30)).columns, 130))


# ------------------------------------------------------------ 绘制工具 ----
def bar(cur: int, total: int, width: int = 34, color: str = C.CYAN) -> str:
    if total <= 0:
        return C.GREY + "─" * width + C.RESET
    ratio = max(0.0, min(1.0, cur / total))
    filled = int(width * ratio)
    b = color + "█" * filled + C.GREY + "░" * (width - filled) + C.RESET
    return f"{b} {C.BOLD}{ratio*100:5.1f}%{C.RESET}"


def rule(ch: str = "─", color: str = C.GREY) -> str:
    return color + ch * w() + C.RESET


def kv(key: str, val: str, klen: int = 14) -> str:
    return f"  {C.GREY}{key:<{klen}}{C.RESET}{val}"


def head(text: str) -> str:
    return f"{C.BOLD}{C.BLUE}▌{C.RESET} {C.BOLD}{text}{C.RESET}"


def pct_color(p: float) -> str:
    return C.GREEN if p >= 1 else (C.YELLOW if p > 0.05 else C.RED)


# ------------------------------------------------------------ 数据收集 ----
class Snapshot:
    """从数据库与运行环境采集一屏所需的所有状态。"""

    def __init__(self, cfg, store: Store):
        self.cfg = cfg
        self.store = store

    def collect(self) -> dict:
        d: dict = {}
        s = self.store
        d["stats"] = s.stats()
        d["bootstrap_done"] = s.bootstrap_done
        d["bootstrap_at"] = s.get_meta("bootstrap_at", "")
        d["last_search"] = s.last_search_at
        d["runs"] = s.recent_runs(6)
        d["pending"] = s.stats()["pending_ai"]
        d["recent_items"] = s.query(only_local=True, limit=5)
        d["cat"] = s.stats()["by_category"]
        d["region"] = s.stats()["by_region"]
        d["db_size"] = self._db_size()
        d["progress"] = self._run_progress()
        return d

    def _db_size(self) -> str:
        try:
            n = self.cfg.db_path.stat().st_size
            return f"{n/1024/1024:.2f} MB"
        except Exception:
            return "-"

    def _run_progress(self) -> dict:
        """从 runs 表推断当前这一轮处于哪个阶段。"""
        runs = self.store.recent_runs(1)
        if not runs:
            return {}
        r = runs[0]
        if r["status"] != "running":
            return {}
        started = r["started_at"]
        try:
            el = (datetime.now() - datetime.fromisoformat(started)).total_seconds()
        except Exception:
            el = 0
        return {"kind": r["kind"], "started": started, "elapsed": el,
                "fetched": r["fetched"], "new": r["new_items"], "id": r["id"]}


# --------------------------------------------------------------- 渲染 ----
def render(cfg, store: Store, snap: Snapshot, spin_i: int = 0) -> str:
    d = snap.collect()
    st = d["stats"]
    W = w()
    L: list[str] = []
    A = L.append

    spin = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[spin_i % 10]

    # ---- 标题栏 ----
    A(rule("═", C.BLUE))
    title = "无人机新规雷达 · UAV Reg Watch"
    status = "运行中" if d["progress"] else "空闲"
    scol = C.YELLOW if d["progress"] else C.GREEN
    pad = W - 2 - len(title) - 8
    A(f" {C.BOLD}{C.CYAN}{title}{C.RESET}" + " " * max(1, pad)
      + f"{spin if d['progress'] else '●'} {scol}{status}{C.RESET} ")
    A(rule("═", C.BLUE))
    A("")

    # ---- 当前任务 ----
    if d["progress"]:
        p = d["progress"]
        A(head("当前任务"))
        A(kv("类型", f"{C.YELLOW}{p['kind']}{C.RESET}"))
        A(kv("开始时间", p["started"][:19]))
        m, sec = divmod(int(p["elapsed"]), 60)
        A(kv("已运行", f"{m}分{sec}秒"))
        A(kv("已抓取", str(p["fetched"])))
        A("")
    else:
        A(head("当前任务"))
        A(kv("状态", f"{C.GREEN}空闲 —— 等待下次定时任务{C.RESET}"))
        A("")

    # ---- 核心指标 ----
    A(head("核心指标"))
    total = st["total"]
    local = st["local"]
    filt = st["filtered"]

    A(kv("累计收录", f"{C.BOLD}{C.WHITE}{total}{C.RESET} 条"))
    if total:
        A(f"  {C.GREY}{'本地相关':<14}{C.RESET}"
          f"{bar(local, total, 30, C.CYAN)}  {C.CYAN}{local}{C.RESET} 条")
        A(f"  {C.GREY}{'已过滤外地':<14}{C.RESET}"
          f"{bar(filt, total, 30, C.RED)}  {C.RED}{filt}{C.RESET} 条")
    A("")

    # ---- 今日进度(每日 200 条目标) ----
    target = int(cfg.schedule.min_daily_items)
    today = st["today_new"]
    A(head(f"今日进度  (目标 {target} 条/日)"))
    col = pct_color(today / target if target else 0)
    A(f"  {bar(today, target, 34, col)}")
    A(f"  {C.GREY}{'今日新增':<14}{C.RESET}{col}{today}{C.RESET} / {target} 条"
      + (f"   {C.GREEN}✓ 已达标{C.RESET}" if today >= target
         else f"   {C.YELLOW}还差 {target-today} 条{C.RESET}"))
    A("")

    # ---- AI 分析进度 ----
    A(head("本地模型分析"))
    pend = d["pending"]
    analyzed = total - pend
    A(f"  {bar(analyzed, total if total else 1, 34, C.MAGENTA)}")
    A(kv("已分析", f"{C.MAGENTA}{analyzed}{C.RESET} 条"))
    if pend:
        A(kv("待处理", f"{C.YELLOW}{pend}{C.RESET} 条"))
    model = cfg.llm.model
    A(kv("模型", f"{C.GREY}{model}{C.RESET}"))
    A("")

    # ---- 基线 ----
    A(head("首次基线"))
    if d["bootstrap_done"]:
        A(kv("状态", f"{C.GREEN}✓ 已完成{C.RESET}"))
        A(kv("完成时间", d["bootstrap_at"][:19]))
    else:
        A(kv("状态", f"{C.YELLOW}○ 未完成 —— 软件将继续首次全量抓取{C.RESET}"))
    A("")

    # ---- 时间线 ----
    A(head("时间线"))
    A(kv("上次搜索", d["last_search"][:19] if d["last_search"] else f"{C.GREY}从未{C.RESET}"))
    hh, mm = (cfg.schedule.daily_time or "07:30").split(":")
    now = datetime.now()
    nxt = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if nxt <= now:
        from datetime import timedelta
        nxt += timedelta(days=1)
    delta = nxt - now
    h2, rem = divmod(int(delta.total_seconds()), 3600)
    m2 = rem // 60
    A(kv("下次搜索", f"{nxt.strftime('%Y-%m-%d %H:%M')}  "
                     f"{C.GREY}({h2}小时{m2}分后){C.RESET}"))
    A(kv("所在地", f"{cfg.location.city or '(未设置)'}"
                  f"  {C.GREY}过滤外地规定: {'开' if True else '关'}{C.RESET}"))
    A("")

    # ---- 分类分布 ----
    if d["cat"]:
        A(head("分类分布"))
        mx = max(d["cat"].values()) or 1
        for k, v in list(d["cat"].items())[:8]:
            bl = int(22 * v / mx)
            A(f"  {C.GREY}{k:<10}{C.RESET}{C.CYAN}{'▇'*bl}{C.RESET} "
              f"{C.BOLD}{v}{C.RESET}")
        A("")

    # ---- 地区分布 ----
    if d["region"]:
        A(head("地区分布 (前 8)"))
        for k, v in list(d["region"].items())[:8]:
            mark = f" {C.GREEN}← 本地{C.RESET}" if k == cfg.location.city else ""
            A(f"  {C.GREY}{k:<10}{C.RESET}{C.BOLD}{v:>5}{C.RESET} 条{mark}")
        A("")

    # ---- 最新条目 ----
    if d["recent_items"]:
        A(head("最新收录"))
        for it in d["recent_items"]:
            imp = int(it["importance"] or 2)
            ic = (C.RED if imp == 3 else C.YELLOW if imp == 2 else C.GREY)
            tag = "重大" if imp == 3 else ("重要" if imp == 2 else "一般")
            t = (it["title"] or "")[:W - 24]
            A(f"  {ic}[{tag}]{C.RESET} {t}")
            A(f"        {C.GREY}{it['source_name'][:22]} · "
              f"{it['region']} · {it['published_at'] or '无日期'}{C.RESET}")
        A("")

    # ---- 运行历史 ----
    if d["runs"]:
        A(head("运行历史"))
        A(f"  {C.GREY}{'类型':<10}{'开始时间':<18}{'抓取':>6}{'新增':>6}"
          f"{'保留':>6}  状态{C.RESET}")
        for r in d["runs"]:
            sc = C.GREEN if r["status"] == "ok" else (
                C.YELLOW if r["status"] == "running" else C.RED)
            A(f"  {r['kind']:<10}{r['started_at'][:16]:<18}"
              f"{r['fetched']:>6}{r['new_items']:>6}{r['kept_local']:>6}  "
              f"{sc}{r['status']}{C.RESET} {C.GREY}{r['note'][:20]}{C.RESET}")
        A("")

    # ---- 底部 ----
    A(rule("─", C.GREY))
    A(f"  {C.GREY}数据库 {d['db_size']}  ·  刷新 {datetime.now().strftime('%H:%M:%S')}"
      f"  ·  Ctrl+C 退出{C.RESET}")
    return "\n".join(L)


# ---------------------------------------------------------------- 主程序 ----
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="无人机新规雷达 终端进度查看器")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="刷新间隔(秒)")
    ap.add_argument("--once", action="store_true", help="打印一次后退出")
    ap.add_argument("--no-color", action="store_true", help="禁用颜色")
    args = ap.parse_args(argv)

    _enable_ansi()
    if args.no_color:
        C.off()

    try:
        cfg = load_config()
    except FileNotFoundError as e:
        print(f"错误: {e}")
        return 1
    store = Store(cfg.db_path)
    snap = Snapshot(cfg, store)

    if args.once:
        print(render(cfg, store, snap))
        return 0

    # 实时模式: 用备用屏幕缓冲区，退出时恢复终端原样
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()
    spin = 0
    try:
        while True:
            out = render(cfg, store, snap, spin)
            sys.stdout.write("\033[H\033[2J" + out + "\n")
            sys.stdout.flush()
            spin += 1
            time.sleep(max(0.2, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()
        print("已退出进度查看器。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
