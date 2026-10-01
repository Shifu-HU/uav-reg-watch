"""设备类型判定与"内容-设备"匹配。

为什么需要它：
    用户是飞手，法规库里大量条目只对特定重量级的设备有效。
    「中型民用无人驾驶航空器系统适航标准」对拿 Mini 的人是噪音，
    而「微型无人驾驶航空器无需实名登记」对拿 Mavic 3 的人也是噪音。
    按设备过滤能让晨报从"200 条泛泛的法规"变成"跟我有关的那几十条"。

分级依据：《无人驾驶航空器飞行管理暂行条例》按**最大起飞重量**分类。
    微型  < 0.25 kg
    轻型  0.25 ~ 4 kg
    小型  4 ~ 15 kg
    中型  15 ~ 116 kg
    大型  > 116 kg（个人几乎用不到，界面不提供）

注意：这里只判重量级，不涉及适航类别（正常类/限用类/特技类），
      那是另一个维度，个人飞手不需要关心。
"""

from __future__ import annotations

import re

# 按重量递增
CATEGORIES = ("微型", "轻型", "小型", "中型", "大型")

CATEGORY_RULES = [
    ("微型", 0.0, 0.25),
    ("轻型", 0.25, 4.0),
    ("小型", 4.0, 15.0),
    ("中型", 15.0, 116.0),
    ("大型", 116.0, float("inf")),
]

# 界面提供的选项（个人飞手用不到大型，但保留以覆盖全）
SELECTABLE = ("微型", "轻型", "小型", "中型")

# 每类的典型说明，帮用户选对
CATEGORY_HINT = {
    "微型": "不到 250 克，如 DJI Mini / Neo 系列",
    "轻型": "250 克~4 公斤，如 Mavic / Air / Phantom 系列",
    "小型": "4~15 公斤，如行业机、植保机",
    "中型": "15~116 公斤，如大型物流机、载人 eVTOL",
    "大型": "116 公斤以上",
}

# 通用 = 与重量级无关，所有飞手都该看
GENERIC = "通用"

# 常见机型的重量，帮用户少填一次（单位 g）
KNOWN_MODELS = {
    "dji mini 4 pro": 249, "dji mini 4k": 246, "dji mini 3": 248,
    "dji neo": 135, "dji avata 2": 377, "dji mavic 3": 895,
    "dji mavic 3 pro": 958, "dji air 3": 720, "dji air 3s": 724,
    "dji mini 2 se": 246, "dji phantom 4": 1380,
    "dji inspire 3": 3995, "dji mavic 2": 907,
    "autel evo lite": 835, "autel evo ii": 1120,
    "dji matrice 30": 3770, "dji matrice 350": 6400,
    "dji agras t40": 38000, "dji agras t50": 40000,
}


# ----------------------------------------------------------------------
def classify_weight(grams: float) -> str:
    """按最大起飞重量返回类别名。无法判定时返回空串。"""
    try:
        g = float(grams)
    except (TypeError, ValueError):
        return ""
    kg = g / 1000.0
    if kg < 0:
        return ""
    for name, lo, hi in CATEGORY_RULES:
        if lo <= kg < hi:
            return name
    return ""


def guess_weight(text: str, heaviest: bool = True) -> float | None:
    """按机型名猜重量，猜不到返回 None。

    heaviest=True 取区间最大值 —— 分级按最坏情况算，
    免得用户以为自己是微型、实际换了长续航电池就超了 250 克。
    """
    t = (text or "").lower().strip()
    if not t:
        return None
    # 先查完整机型库（含区间），再退回简易表
    try:
        from .models_db import _FLAT
        for k, (lo, hi) in _FLAT.items():
            if t == k or k in t or t in k:
                return float(hi if heaviest else lo)
    except Exception:                      # noqa: BLE001
        pass
    if t in KNOWN_MODELS:
        return float(KNOWN_MODELS[t])
    for k, v in KNOWN_MODELS.items():
        if k in t or t in k:
            return float(v)
    return None


# ----------------------------------------------------------------------
# 条目 -> 适用类别
# 这些正则匹配正文/标题里出现的重量级限定词
_LEVEL_WORDS = {
    "微型": r"微型",
    "轻型": r"轻型",
    "小型": r"小型",
    "中型": r"中型",
    "大型": r"大型",
}

# 出现这些说明是分了重量级的具体规定，而不是普适规则
_HAS_LEVEL = re.compile("|".join(_LEVEL_WORDS.values()))

# 复合词 —— 中文里"微轻型"是一个整体概念（法规名里常见），
# 直接按"轻型"匹配会把整条法规误判成只适用于轻型设备。
# 这类词出现时应视为**覆盖多个级别**，而不是其中某一个。
_COMPOUND = {
    r"微轻型": ["微型", "轻型"],
    r"微轻小型": ["微型", "轻型", "小型"],
    r"轻小型": ["轻型", "小型"],
    r"中小型": ["小型", "中型"],
    r"大中小型": ["小型", "中型", "大型"],
}


def applicable_categories(text: str) -> list[str]:
    """判断一条内容适用于哪些设备类别。

    返回 ["通用"] 表示与重量级无关，所有飞手都该看。
    返回 ["中型"] 表示只对中型设备有效。
    返回 ["轻型","小型"] 表示同时覆盖两类。
    """
    t = text or ""
    if not t:
        return [GENERIC]

    # 先处理复合词：把命中的复合词从文本里摘掉，
    # 再按单级别匹配，避免"微轻型"同时被算成"微型"和"轻型"之外的误判
    rest = t
    compound_hits: list[str] = []
    for pat, names in _COMPOUND.items():
        if re.search(pat, rest):
            compound_hits.extend(names)
            rest = re.sub(pat, " ", rest)

    hits = [name for name, pat in _LEVEL_WORDS.items()
            if re.search(pat, rest)]
    for n in compound_hits:
        if n not in hits:
            hits.append(n)

    # 明确点了重量级只针对某一类，且没有"各/所有/均"这类全称词
    if hits and not re.search(r"各(?:类|级)|所有|均可|均适用|全部", t):
        # 「小型、中型民用无人驾驶航空器操控员训练要求」这种列举式
        return hits
    return [GENERIC]


def matches_device(cats: list[str], my_cats: list[str]) -> bool:
    """内容是否与用户设备相关。

    通用内容永远相关；带级别的内容只要命中用户任一设备即可。
    用户没填设备时一律相关（不能因为没设置就什么都看不到）。
    """
    if not my_cats:
        return True
    if not cats or GENERIC in cats:
        return True
    return bool(set(cats) & set(my_cats))


def label_for(cats: list[str]) -> str:
    """给界面用的短标签。"""
    if not cats or cats == [GENERIC]:
        return GENERIC
    return "·".join(cats)
