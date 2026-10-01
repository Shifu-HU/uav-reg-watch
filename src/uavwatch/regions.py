"""中国省市区数据，供界面下拉菜单使用。

数据来源: china-division (含 31 省 / 340+ 地级市 / 2800+ 区县)。
对地理过滤而言只需要到"市"一级 —— 无人机法规通常按市发布，
区县一级极少独立发文，但界面上仍提供以便用户精确定位。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "regions.json"


@lru_cache(maxsize=1)
def _raw() -> list[dict]:
    try:
        return json.loads(_DATA.read_text(encoding="utf-8"))
    except Exception:                     # noqa: BLE001
        return []


def _strip(name: str) -> str:
    """去掉行政区后缀 —— 地理过滤匹配的是"深圳"而不是"深圳市"。"""
    for suf in ("特别行政区", "维吾尔自治区", "回族自治区", "壮族自治区",
                "自治区", "省", "市辖区", "地区", "自治州", "盟",
                "区", "县", "市"):
        if name.endswith(suf) and len(name) > len(suf):
            return name[: -len(suf)]
    return name


@lru_cache(maxsize=1)
def provinces() -> list[tuple[str, str]]:
    """[(显示名, 匹配名)]，如 ("广东省", "广东")。"""
    return [(p["name"], _strip(p["name"])) for p in _raw()]


@lru_cache(maxsize=64)
def cities(province_display: str) -> list[tuple[str, str]]:
    """某省下的地级市。直辖市的"市辖区"会展开为市名本身。"""
    for p in _raw():
        if p["name"] != province_display:
            continue
        out: list[tuple[str, str]] = []
        pname = _strip(p["name"])
        for c in p.get("children", []):
            cname = c["name"]
            # 直辖市/特殊结构: "市辖区" 这类占位名无意义，用省名代替
            if cname in ("市辖区", "县", "省直辖县级行政区划", "自治区直辖县级行政区划"):
                continue
            out.append((cname, _strip(cname)))
        # 直辖市下面被跳过了 -> 至少给出省名本身作为一个"市"
        if not out:
            out.append((p["name"], pname))
        return out
    return []


@lru_cache(maxsize=256)
def districts(province_display: str, city_display: str) -> list[tuple[str, str]]:
    """某市下的区县。"""
    for p in _raw():
        if p["name"] != province_display:
            continue
        for c in p.get("children", []):
            if c["name"] != city_display:
                continue
            return [(d["name"], _strip(d["name"])) for d in c.get("children", [])]
    return []


@lru_cache(maxsize=1)
def all_city_names() -> set[str]:
    """全部地级市的匹配名 —— 地理过滤用它识别"这是哪个城市"。"""
    out: set[str] = set()
    for p in _raw():
        pname = _strip(p["name"])
        # 直辖市本身就是一个"市"(北京市/上海市…)，不能只收集它的下级
        if len(pname) >= 2:
            out.add(pname)
        for c in p.get("children", []):
            n = _strip(c["name"])
            if len(n) >= 2 and n not in ("市辖", "省直辖县级行政单位",
                                         "自治区直辖县级行政单位"):
                out.add(n)
    return out


def find_province(city_match: str) -> str:
    """按城市匹配名反查省份显示名。"""
    for p in _raw():
        for c in p.get("children", []):
            if _strip(c["name"]) == city_match:
                return p["name"]
    return ""


def find_city(province_display: str, city_match: str) -> str:
    """在指定省份下按匹配名查找城市显示名。"""
    for disp, match in cities(province_display):
        if match == city_match:
            return disp
    return ""
