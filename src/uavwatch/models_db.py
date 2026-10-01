"""机型知识库 —— 品牌 / 系列 / 机型三级，含起飞重量。

为什么要这么细：
    用户不可能记得自己那台多少克。让他从下拉里点品牌→系列→机型，
    重量自动带出来，级别自动算好，这才是"能用"的设备栏。

数据口径：**最大起飞重量**（含电池，不含额外负载），单位克。
    来源为各厂商官网规格页。同一机型不同版本重量不同时，
    取**较重**的那个 —— 分级按最坏情况算，免得用户以为自己是微型
    实际超了 250 克。
"""

from __future__ import annotations

# 品牌 -> 系列 -> [(机型, 重量g)]
# 顺序即界面显示顺序，按常见程度排。
MODELS: dict[str, dict[str, list[tuple[str, int]]]] = {
    "大疆 DJI": {
        # Mini 系列都要写区间：标准电池都在 249 克上下，换长续航
        # 电池就过 250 克了 —— 一台机器同时受"微型"和"轻型"两套
        # 规定约束，只写一个数字会漏一半法规。
        "Mini 系列（微型）": [
            ("Mini 5 Pro", 249, 310),     # 官网：标准 249.9 / 最大约 310 克
            ("Mini 4 Pro", 249, 289),
            ("Mini 4K", 246, 246),
            ("Mini 3", 248, 248),
            ("Mini 3 Pro", 249, 290),
            ("Mini 2", 249, 249),
            ("Mini 2 SE", 246, 246),
            ("Mini SE", 249, 249),
            ("Mini", 249, 249),
        ],
        # Lito 是大疆面向入门市场的新系列（2026-04 发布），
        # 两个型号都是 sub-249g。
        "Lito 系列": [
            ("Lito X1", 249, 310),
            ("Lito 1", 249, 310),
        ],
        "Neo / Flip 系列": [
            ("Neo 2", 151, 151),          # 官网：151 克（不含数字图传模块）
            ("Neo", 135, 135),
            ("Flip", 249, 249),
        ],
        "Air 系列": [
            ("Air 3S", 724),
            ("Air 3", 720),
            ("Air 2S", 595),
            ("Air 2", 570),
            ("Mavic Air", 430),
        ],
        "Mavic 系列": [
            ("Mavic 4 Pro", 1063),
            ("Mavic 3 Pro", 958),
            ("Mavic 3 Classic", 895),
            ("Mavic 3", 895),
            ("Mavic 2 Pro", 907),
            ("Mavic 2 Zoom", 905),
            ("Mavic Pro", 734),
        ],
        "Avata 穿越机": [
            ("Avata 360", 455),           # 官网：455 克（全景穿越机）
            ("Avata 2", 377),
            ("Avata", 410),
        ],
        "Phantom 系列": [
            ("Phantom 4 Pro V2.0", 1375),
            ("Phantom 4 Pro", 1388),
            ("Phantom 4 Advanced", 1368),
            ("Phantom 4", 1380),
        ],
        "Inspire 系列": [
            ("Inspire 3", 3995),
            ("Inspire 2", 4250),
            ("Inspire 1", 3060),
        ],
        "Matrice 行业机": [
            ("Matrice 4E", 1219),
            ("Matrice 4T", 1233),
            ("Mavic 3M", 1050),           # 多光谱行业机，官网 1050 克
            ("Matrice 350 RTK", 6470),
            ("Matrice 300 RTK", 6300),
            ("Matrice 30T", 3770),
            ("Matrice 30", 3770),
        ],
        "Agras 植保机": [
            ("Agras T50", 40000),
            ("Agras T40", 38000),
            ("Agras T30", 26800),
            ("Agras T25", 24800),
            ("Agras T20P", 22800),
        ],
    },
    "道通 Autel": {
        "EVO Lite 系列": [
            ("EVO Lite+", 835),
            ("EVO Lite", 835),
        ],
        "EVO Nano 系列": [
            ("EVO Nano+", 249),
            ("EVO Nano", 249),
        ],
        "EVO II 系列": [
            ("EVO II Pro V3", 1191),
            ("EVO II Dual 640T", 1191),
            ("EVO II", 1127),
        ],
        "EVO Max 系列": [
            ("EVO Max 4T", 1620),
            ("EVO Max 4N", 1620),
        ],
    },
    "影翎 / 其他消费机": {
        "哈博森 Hubsan": [
            ("Zino Pro+", 700),
            ("Zino 2+", 680),
            ("Zino Mini Pro", 249),
        ],
        "飞米 FIMI": [
            ("X8 SE 2022", 765),
            ("X8 SE", 765),
            ("X8 Mini V2", 245),
        ],
        "零零科技": [
            ("Hover 2", 500),
            ("Hover X1", 192),
        ],
        "影石 Insta360": [
            ("Antigravity A1", 249),
        ],
    },
    "行业 / 测绘": {
        "华测 CHCNAV": [
            ("P330 Pro", 3900),
        ],
        "中海达": [
            ("iFly D6", 4200),
        ],
        "纵横股份": [
            ("CW-15", 25000),
            ("CW-25E", 43000),
        ],
    },
    "自组 / 其他": {
        "通用": [
            ("F450 自组机", 1400),
            ("F550 自组机", 2200),
            ("穿越机 5 寸", 700),
            ("穿越机 3 寸", 350),
            ("固定翼 小", 2000),
        ],
    },
}


def _span(item) -> tuple[int, int]:
    """把一条机型记录统一成 (最小克, 最大克)。

    两种写法都支持：
        ("Mini 4 Pro", 249)          -> (249, 249)   固定重量
        ("Mini 5 Pro", 249, 310)     -> (249, 310)   有重量跨度

    为什么要跨度：
        Mini 5 Pro 标准起飞重量 249.9 克，换长续航电池后约 310 克 ——
        同一台机器**横跨微型和轻型两条法规线**。用户拿它既能飞
        "微型免实名"的场景，也可能落到轻型的要求里。只存一个数字
        必然漏掉一半相关规定，所以按区间存，两级消息都收。
    """
    if len(item) >= 3:
        return int(item[1]), int(item[2])
    g = int(item[1])
    return g, g


def all_models() -> list[tuple[str, str, str, int, int]]:
    """展平成 (品牌, 系列, 机型, 最小克, 最大克) 列表。"""
    out = []
    for brand, series_map in MODELS.items():
        for series, items in series_map.items():
            for item in items:
                lo, hi = _span(item)
                out.append((brand, series, item[0], lo, hi))
    return out


def model_levels(item) -> list[str]:
    """一台机器覆盖的重量级别 —— 跨级时会返回两个。

    Mini 5 Pro(249~310) -> ["微型", "轻型"]
    Mavic 3(895)        -> ["轻型"]
    """
    from .devices import classify_weight
    lo, hi = _span(item)
    out = []
    for g in (lo, hi):
        lv = classify_weight(g)
        if lv and lv not in out:
            out.append(lv)
    return out


# ----------------------------------------------------------------------
# 外部增量库 —— 让机型数据能脱离源码滚动更新
#
# 自动更新程序（models_update.py）抓到的新机型写到这里，运行时
# 自动并进 MODELS。好处是不用改 .py、不用重新打包 exe 就能让
# 用户拿到新机型。
#
# 格式（JSON）：
#   {"version": "2026-09-20",
#    "brands": {"大疆 DJI": {"Mini 系列（微型）": [["Mini 6", 249, 249]]}}}
#
# 冲突时**以外部文件为准** —— 它更新。同名的以外部为准。
# ----------------------------------------------------------------------
EXTRA_PATH = None      # 由 app 启动时注入；None 表示不加载


def load_extra(path) -> int:
    """把外部 JSON 并进 MODELS。返回并入的机型数。"""
    import json
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                      # noqa: BLE001
        return 0
    n = 0
    for brand, series_map in (data.get("brands") or {}).items():
        bucket = MODELS.setdefault(brand, {})
        for series, items in series_map.items():
            lst = bucket.setdefault(series, [])
            # 已有同名的先删掉，再放新的（外部的更新）
            names = {it[0] for it in items}
            lst[:] = [x for x in lst if x[0] not in names]
            for it in items:
                if len(it) >= 3:
                    lst.append((it[0], int(it[1]), int(it[2])))
                    n += 1
                elif len(it) == 2:
                    lst.append((it[0], int(it[1])))
                    n += 1
    return n


# 扁平索引，供模糊匹配用：小写机型名 -> (最小, 最大)
_FLAT: dict[str, tuple[int, int]] = {}
for _b, _s, _n, _lo, _hi in all_models():
    _FLAT[_n.lower()] = (_lo, _hi)
    # 去掉品牌前缀的写法
    for _p in ("dji ", "autel "):
        if _n.lower().startswith(_p):
            _FLAT[_n.lower()[len(_p):]] = (_lo, _hi)


def rebuild_index() -> int:
    """并入外部数据后重建扁平索引。返回当前机型总数。"""
    _FLAT.clear()
    rows = all_models()
    for _b, _s, _n, _lo, _hi in rows:
        _FLAT[_n.lower()] = (_lo, _hi)
        for _p in ("dji ", "autel "):
            if _n.lower().startswith(_p):
                _FLAT[_n.lower()[len(_p):]] = (_lo, _hi)
    return len(rows)
