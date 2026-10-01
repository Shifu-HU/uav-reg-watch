"""命令行入口: 无界面运行 / 定时守护 / 手动抓取。

用法:
    python -m uavwatch.cli run          # 跑一轮增量搜索
    python -m uavwatch.cli bootstrap    # 首次全量基线
    python -m uavwatch.cli daemon       # 常驻，按 config 定时执行
    python -m uavwatch.cli brief        # 生成今日简报
    python -m uavwatch.cli status       # 查看状态
    python -m uavwatch.cli doctor       # 检查环境
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date

from .config import load_config
from .llm import LocalLLM
from .notify import Notifier
from .pipeline import Pipeline
from .scheduler import DailyScheduler
from .storage import Store


def _setup_log(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_run(args) -> int:
    cfg, store = load_config(), None
    store = Store(cfg.db_path)
    kind = "manual"
    p = Pipeline(cfg, store, progress=lambda s, c, t: print(f"  >> {s} {c}/{t}", flush=True))
    res = p.run(kind)
    print()
    print("=== 完成 ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0


def cmd_bootstrap(args) -> int:
    cfg = load_config()
    store = Store(cfg.db_path)
    if store.bootstrap_done and not args.force:
        print("首次基线已完成。加 --force 可强制重跑。")
        return 0
    p = Pipeline(cfg, store, progress=lambda s, c, t: print(f"  >> {s} {c}/{t}", flush=True))
    res = p.run("bootstrap")
    print()
    print("=== 基线建立完成 ===")
    for k, v in res.items():
        print(f"  {k}: {v}")
    return 0


def cmd_daemon(args) -> int:
    cfg = load_config()
    store = Store(cfg.db_path)

    def run_fn(kind: str) -> dict:
        p = Pipeline(cfg, store, progress=lambda s, c, t: print(f"  >> {s} {c}/{t}", flush=True))
        res = p.run(kind)
        try:
            Notifier(cfg, store, cfg.data_dir).build_brief()
        except Exception:                 # noqa: BLE001
            pass
        return res

    def on_event(evt: str, data: dict) -> None:
        print(f"[事件] {evt}: {data}", flush=True)

    sch = DailyScheduler(cfg, store, run_fn, on_event=on_event)
    print(f"守护模式已启动。每日 {cfg.schedule.daily_time} 执行。Ctrl+C 退出。")
    sch.start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        sch.stop()
        print("\n已退出。")
    return 0


def cmd_brief(args) -> int:
    """生成晨报。--open 会同时用浏览器打开。"""
    import webbrowser
    from .morning import MorningBrief

    cfg = load_config()
    store = Store(cfg.db_path)
    brief = MorningBrief(cfg, store, cfg.data_dir)
    md, html, txt, d = brief.build(args.day or "")

    print("晨报已生成:")
    print(f"  HTML : {html}")
    print(f"  MD   : {md}")
    print(f"  TXT  : {txt}")
    print()
    print(f"  {brief.headline()}")
    print(f"  可推送 {len(d['all'])} 条 / 目标 {d['quota']}"
          f"  (今日新增 {len(d['fresh'])} + 历史补课 {len(d['backlog'])})")
    print(f"  必读 {len(d['must'])} · 关注 {len(d['watch'])} · 参考 {len(d['ref'])}")
    if d["soon"]:
        print("  时效提醒:")
        for r in d["soon"][:5]:
            print(f"    {brief._when_text(r):14} {r['title'][:44]}")
    if getattr(args, "open", False):
        webbrowser.open(html.resolve().as_uri())
        print()
        print("已在浏览器中打开。")
    return 0


def cmd_onopen(args) -> int:
    """模拟"用户打开软件"时的补搜判断。"""
    cfg = load_config()
    store = Store(cfg.db_path)
    sch = DailyScheduler(cfg, store, run_fn=lambda k: Pipeline(cfg, store).run(k))
    fired, why = sch.on_app_open()
    print(("已触发搜索: " if fired else "无需搜索: ") + why)
    if fired:
        print("(在真实界面中这会后台执行，不阻塞使用)")
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    store = Store(cfg.db_path)
    st = store.stats()
    print("=== 无人机新规雷达 · 状态 ===")
    print(f"  所在地        : {cfg.location.city or '(未设置)'} / {cfg.location.province or '-'}")
    print(f"  首次基线      : {'已完成 @ ' + store.get_meta('bootstrap_at') if store.bootstrap_done else '未完成'}")
    print(f"  上次搜索      : {store.last_search_at or '从未'}")
    print(f"  累计收录      : {st['total']} 条")
    print(f"  本地相关      : {st['local']} 条")
    print(f"  已过滤外地    : {st['filtered']} 条")
    print(f"  今日新增      : {st['today_new']} 条 (目标 {cfg.schedule.min_daily_items})")
    print(f"  待 AI 分析    : {st['pending_ai']} 条")
    print()
    print("  按分类:")
    for k, v in st["by_category"].items():
        print(f"    {k:<10} {v}")
    print()
    print("  按地区 (前 10):")
    for k, v in list(st["by_region"].items())[:10]:
        print(f"    {k:<10} {v}")
    print()
    print("  最近运行:")
    for r in store.recent_runs(8):
        print(f"    [{r['kind']:<9}] {r['started_at'][:16]}  抓取{r['fetched']:<4} "
              f"新增{r['new_items']:<4} 保留{r['kept_local']:<4} {r['status']}")
    return 0


def cmd_doctor(args) -> int:
    cfg = load_config()
    store = Store(cfg.db_path)
    print("=== 环境自检 ===")

    llm = LocalLLM(cfg.llm)
    ok, msg = llm.probe()
    print(f"  [{'OK ' if ok else 'FAIL'}] 本地模型: {msg}")
    if ok:
        models = llm.list_models()
        print(f"         已安装: {', '.join(models)}")

    from .fetcher import Fetcher
    from .sources.searxng import SearxngSource
    f = Fetcher(delay=0, timeout=8)
    sx = SearxngSource(cfg.sources.get("searxng", {}), f)
    ok2, msg2 = sx.health()
    print(f"  [{'OK ' if ok2 else 'WARN'}] SearXNG : {msg2}")

    try:
        html = f.post_text("https://www.caac.gov.cn/was5/web/search",
                           {"channelid": "211383", "sw": "无人机", "perpage": "10",
                            "page": "1", "selST": "All"})
        ok3 = "td" in html and len(html) > 5000
        print(f"  [{'OK ' if ok3 else 'FAIL'}] 民航局   : 检索接口返回 {len(html)} 字节")
    except Exception as e:                # noqa: BLE001
        print(f"  [FAIL] 民航局   : {e}")
    f.close()

    print(f"  [OK ] 数据库   : {cfg.db_path}")
    print(f"  [OK ] 数据目录 : {cfg.data_dir}")
    missing = []
    try:
        import PySide6  # noqa: F401
    except ImportError:
        missing.append("PySide6 (桌面界面)")
    try:
        import yaml  # noqa: F401
    except ImportError:
        missing.append("pyyaml")
    try:
        import bs4  # noqa: F401
    except ImportError:
        missing.append("beautifulsoup4")
    if missing:
        print(f"  [WARN] 缺失依赖: {', '.join(missing)}")
    else:
        print("  [OK ] Python 依赖齐全")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="uavwatch", description="无人机新规雷达 CLI")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("run", help="跑一轮增量搜索").set_defaults(func=cmd_run)

    b = sub.add_parser("bootstrap", help="首次全量基线")
    b.add_argument("--force", action="store_true")
    b.set_defaults(func=cmd_bootstrap)

    sub.add_parser("daemon", help="常驻定时执行").set_defaults(func=cmd_daemon)

    br = sub.add_parser("brief", help="生成今日晨报")
    br.add_argument("--day", default="")
    br.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    br.set_defaults(func=cmd_brief)

    mo = sub.add_parser("morning", help="同 brief（晨报）")
    mo.add_argument("--day", default="")
    mo.add_argument("--open", action="store_true")
    mo.set_defaults(func=cmd_brief)

    op = sub.add_parser("open", help="开屏补搜判断（模拟打开软件）")
    op.set_defaults(func=cmd_onopen)

    sub.add_parser("status", help="查看状态").set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    _setup_log(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
