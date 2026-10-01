"""机型库自动更新 —— 多品牌抓取最新机型。

为什么要这个：
    DJI 半年出一批新机（Mini 5 Pro、Lito、Avata 360 都这么来的），
    手工维护 models_db.py 一定滞后。用户反馈"机型信息不新"就是这个。

抓取策略 —— **官方优先，搜索兜底**：
    1. 官方 sitemap：能拿到权威重量（DJI 的开放，其余大多 403）
    2. SearXNG 搜索：抓不到官网时的兜底，从评测站提取
    3. 只**报告**差异，加 --write 才提示写回

为什么默认不写：
    重量直接决定法规分级（249g 和 251g 差一个级别），
    自动抓错了后果比"信息旧"严重。人工过一眼再写。

用法：
    python -m uavwatch.models_update              # 全品牌扫描
    python -m uavwatch.models_update --brand dji  # 只扫某个品牌
"""

from __future__ import annotations

import gzip
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

SEARX = "http://127.0.0.1:8080/search"


# ----------------------------------------------------------------------
# 品牌配置
#
# sitemap：官方站点地图（能抓到就给权威数据）
# specs_re：从 sitemap 里挑出产品规格页的路径模式
# search_q：官网抓不到时的搜索词
# 有些品牌（Autel / Hubsan / FIMI）会 403 挡爬虫，那就只能走搜索。
# ----------------------------------------------------------------------
BRANDS: dict[str, dict] = {
    "dji": {
        "label": "大疆 DJI",
        "sitemap": "https://www.dji.com/sitemap.xml",
        "specs_re": re.compile(r"/cn/([a-z0-9-]+)/specs", re.I),
        "spec_url": "https://www.dji.com/cn/{slug}/specs",
        "skip": re.compile(r"osmo|pocket|action|mic|ronin|rs-?[234]|focus|"
                           r"transmission|power|dock|goggles|controller|rc-|"
                           r"mobile|om-|dji-care|store|app|support|news|"
                           r"service|where-to-buy|o[34]-air-unit"),
        "drone": re.compile(r"mini|mavic|air|neo|flip|lito|avata|inspire|"
                            r"matrice|phantom|agras|spark|tello", re.I),
        "search_q": "",
    },
    "autel": {
        "label": "道通 Autel",
        "sitemap": "https://www.autelrobotics.com/sitemap.xml",   # 实测 403
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "Autel Robotics EVO drone specifications takeoff weight",
    },
    "hubsan": {
        "label": "哈博森 Hubsan",
        "sitemap": "https://www.hubsan.com/sitemap.xml",          # 实测不通
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "Hubsan drone specifications takeoff weight Zino",
    },
    "fimi": {
        "label": "飞米 FIMI",
        "sitemap": "https://www.fimi.com/sitemap.xml",            # 实测 403
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "FIMI drone X8 specifications takeoff weight grams",
    },
    "insta360": {
        "label": "影石 Insta360",
        "sitemap": "https://www.insta360.com/sitemap.xml",
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "Insta360 Antigravity drone takeoff weight grams specs",
    },
    "zerozero": {
        "label": "零零科技",
        "sitemap": "",
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "零零科技 Hover 无人机 起飞重量 克",
    },
    "chcnav": {
        "label": "华测 CHCNAV",
        "sitemap": "",
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "华测 CHCNAV 无人机 起飞重量 规格",
    },
    "auterion": {
        "label": "纵横股份",
        "sitemap": "",
        "specs_re": None,
        "spec_url": "",
        "skip": None,
        "drone": None,
        "search_q": "纵横股份 CW 无人机 起飞重量 规格",
    },
}


def _get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", "ignore")


# ----------------------------------------------------------------------
# 重量解析 —— 各家页面写法不同，多套模式依次试
# ----------------------------------------------------------------------
WEIGHT_PATTERNS = [
    # 大疆官网的结构化 HTML
    re.compile(r"起飞重量</h4></li><li class=\"detailed-parameter\">"
               r"<div class=\"detailed-parameter-value\">([^<]{0,240})", re.I),
    # 通用："起飞重量：xxx 克" / "Takeoff Weight: xxx g"
    re.compile(r"(?:标准起飞重量|最大起飞重量|起飞重量|Takeoff Weight|"
               r"Weight)\s*[:：]?\s*(?:约\s*|about\s*)?"
               r"([0-9][0-9.,]*)\s*(?:克|g\b|grams?\b)", re.I),
    # 表格/列表里裸写 "249 g"
    re.compile(r"([0-9]{2,4}(?:\.[0-9])?)\s*(?:克|g\b|grams?\b)", re.I),
]


def parse_weight(text: str) -> tuple[int, int] | None:
    """解析起飞重量，返回 (最小, 最大)。

    一页里常同时有"标准起飞重量 249.9 克"和"最大起飞重量约 310 克"，
    这就是我们要的区间 —— 取这两个数。只有一个数时上下限相同。

    **不做向上取整**：249.9 → 249。取整成 250 会把 Mini 5 Pro 从
    微型推到轻型，法规含义完全不同。
    """
    for pat in WEIGHT_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        nums = []
        for x in re.findall(r"([0-9]+(?:\.[0-9]+)?)", m.group(1)):
            try:
                v = float(x)
            except ValueError:
                continue
            if 20 <= v <= 150000:
                nums.append(int(v))
        if nums:
            return min(nums), max(nums)
    return None


def pretty(slug: str) -> str:
    """dji slug -> 展示名。mini-5-pro -> Mini 5 Pro / air-3s -> Air 3S"""
    words = slug.split("-")
    out = []
    for i, w in enumerate(words):
        if w in ("rtk", "se", "dji"):
            out.append(w.upper())
        elif i > 0 and w in ("s", "x", "x1", "c", "k", "m", "e", "t", "n"):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


# ----------------------------------------------------------------------
def scan_official(brand: str, cfg: dict) -> dict[str, tuple[int, int]]:
    """从品牌官网 sitemap 抓规格页。抓不到就返回空。"""
    if not cfg.get("sitemap") or not cfg.get("specs_re"):
        return {}
    try:
        idx = _get(cfg["sitemap"])
    except Exception as e:                 # noqa: BLE001
        print(f"  [{cfg['label']}] sitemap 不可用（{str(e)[:40]}）")
        return {}

    shards = re.findall(r"<loc>([^<]+\.xml\.gz)</loc>", idx)
    slugs: set[str] = set()
    for s in shards:
        try:
            body = _get(s, timeout=40)
        except Exception:                  # noqa: BLE001
            continue
        for m in cfg["specs_re"].finditer(body):
            slug = m.group(1).lower()
            if cfg["skip"] and cfg["skip"].search(slug):
                continue
            if cfg["drone"] and not cfg["drone"].search(slug):
                continue
            slugs.add(slug)

    out: dict[str, tuple[int, int]] = {}
    for slug in sorted(slugs):
        url = cfg["spec_url"].format(slug=slug)
        try:
            html_text = _get(url)
        except Exception:                  # noqa: BLE001
            continue
        span = parse_weight(html_text)
        if span:
            out[pretty(slug)] = span
    return out


def scan_search(brand: str, cfg: dict) -> dict[str, tuple[int, int]]:
    """官网抓不到时走 SearXNG。返回 {} 表示不可用。"""
    q = cfg.get("search_q")
    if not q:
        return {}
    url = f"{SEARX}?q={urllib.parse.quote(q)}&format=json"
    try:
        raw = _get(url, timeout=15)
    except Exception:                      # noqa: BLE001
        return {}
    # 从搜索结果摘要里捞 "xxx g / xxx 克"
    out: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r"([A-Z][A-Za-z0-9+ ]{2,22}?)"
                         r"[^.]{0,60}?([0-9]{2,4}(?:\.[0-9])?)\s*(?:克|g\b)",
                         raw):
        name, g = m.group(1).strip(), int(float(m.group(2)))
        if 20 <= g <= 150000:
            out.setdefault(name, (g, g))
    return out


def main(argv: list[str]) -> int:
    from .models_db import all_models
    have_rows = all_models()
    have_key = {re.sub(r"[^a-z0-9]", "", n.lower()) for _b, _s, n, _l, _h in have_rows}
    only = ""
    for i, a in enumerate(argv):
        if a == "--brand" and i + 1 < len(argv):
            only = argv[i + 1].lower()

    print("机型库自动更新（多品牌）")
    print(f"  本地已有 {len(have_rows)} 款")
    print()

    targets = {k: v for k, v in BRANDS.items() if not only or k == only}
    found: dict[str, dict[str, tuple[int, int]]] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(scan_official, k, v): k for k, v in targets.items()}
        for f in as_completed(futs):
            k = futs[f]
            try:
                res = f.result()
            except Exception as e:         # noqa: BLE001
                print(f"  [{BRANDS[k]['label']}] 抓取异常: {str(e)[:50]}")
                res = {}
            found[k] = res
            print(f"  [{BRANDS[k]['label']}] 官网 {len(res)} 款")

    # 官网没抓到的，走搜索兜底
    print()
    for k, cfg in targets.items():
        if found.get(k):
            continue
        res = scan_search(k, cfg)
        found[k] = res
        how = "搜索" if res else "无来源"
        print(f"  [{cfg['label']}] {how} {len(res)} 款")

    print()
    print("=" * 60)
    total_new = 0
    for k, res in found.items():
        new = {n: span for n, span in res.items()
               if re.sub(r"[^a-z0-9]", "", n.lower()) not in have_key}
        if not new:
            continue
        total_new += len(new)
        print(f"{BRANDS[k]['label']} 发现 {len(new)} 款新机型：")
        for n, (lo, hi) in sorted(new.items()):
            rng = f"{lo} g" if lo == hi else f"{lo} ~ {hi} g"
            print(f"    {n:<26} {rng}")
        print()

    if not total_new:
        print("没有发现新机型。")
        return 0
    print(f"共 {total_new} 款本地没有的机型。")

    if "--write" not in argv:
        print()
        print("这只是预览。加 --write 写入外部增量库 (data/models_extra.json)，")
        print("运行时自动并入，不用改源码也不用重新打包。")
        return 0

    # 写入外部增量库 —— 不动源码
    import json
    from pathlib import Path
    from .models_db import MODELS

    # 按品牌归到已有系列下；找不到就新建一个系列
    out_brands: dict[str, dict[str, list]] = {}
    for k, res in found.items():
        label = BRANDS[k]["label"]
        series_map = out_brands.setdefault(label, {})
        # 本地已有这个品牌的哪些系列
        known = list(MODELS.get(label, {}).keys())
        for n, (lo, hi) in res.items():
            key2 = re.sub(r"[^a-z0-9]", "", n.lower())
            if key2 in have_key:
                continue
            # 猜系列：取机型名第一个词
            head = n.split()[0] if n.split() else "其他"
            series = next((s for s in known if head.lower() in s.lower()),
                          f"{head} 系列")
            series_map.setdefault(series, []).append([n, lo, hi])

    out_brands = {b: s for b, s in out_brands.items() if s}
    if not out_brands:
        print("没有可写入的内容。")
        return 0

    path = Path("data") / "models_extra.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8")).get("brands", {})
        except Exception:                  # noqa: BLE001
            old = {}
    # 合并：同一机型以新抓到的为准
    for b, sm in out_brands.items():
        tgt = old.setdefault(b, {})
        for s, items in sm.items():
            lst = tgt.setdefault(s, [])
            names = {it[0] for it in items}
            tgt[s] = [x for x in lst if x[0] not in names] + items

    from datetime import date as _d
    path.write_text(json.dumps({"version": _d.today().isoformat(),
                                "brands": old},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"已写入 {path}")
    print("重启应用后生效（运行时自动并入）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
