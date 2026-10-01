"""天气服务 —— Open-Meteo（完全免费、无需申请任何 key）。

用户明确要求："可以的话接个天气服务，免费那种就好，实在不行用搜索
模块""不要用需要自己申请的 api"。Open-Meteo 两条都满足：
  - api.open-meteo.com    天气预报（当前风速/阵风/温度）
  - geocoding-api.open-meteo.com  中文地名 -> 经纬度
两家都不注册、不鉴权，打包出去谁下载谁就能用。

失败兜底：请求失败时回落到 Bing 搜索结果页抓 "<城市> 天气" 文本，
尽力正则出 "风力 x 级 / 风速 x km/h"；再失败就返回 None，
界面显示"天气暂不可用"，绝不能因此崩掉晨报。

结果按 (城市, 小时) 缓存到 data/weather.json —— 晨报一天生成几次
不必每次都出网。
"""
from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import httpx


@dataclass
class Weather:
    city: str
    temp_c: float          # 气温 ℃
    wind_ms: float         # 平均风速 m/s（10 米高度）
    gust_ms: float         # 阵风 m/s
    wind_dir: int          # 风向（度，0=北）
    code: int              # WMO 天气码
    source: str = "open-meteo"   # open-meteo | bing
    fetched_at: str = ""

    # WMO 码 -> 中文短语（只列常见的，其余显示"多云/晴"大意）
    _CODES = {0: "晴", 1: "基本晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇",
              51: "毛毛雨", 53: "毛毛雨", 55: "毛毛雨",
              61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
              71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "阵雨",
              82: "强阵雨", 85: "阵雪", 86: "阵雪", 95: "雷雨",
              96: "雷雨伴冰雹", 99: "雷雨伴冰雹"}

    @property
    def text(self) -> str:
        return self._CODES.get(self.code, "天气")

    @property
    def dir_text(self) -> str:
        dirs = ("北", "东北", "东", "东南", "南", "西南", "西", "西北")
        return dirs[int((self.wind_dir % 360) / 45)]



# ---------------------------------------------------------------------------
# 续航估算
# ---------------------------------------------------------------------------
# 机型 -> (标称悬停/最大续航 分钟, 备注)。官方规格页数字。
# 没收录的机型不算续航 —— 宁可不显示，不编数。
ENDURANCE: dict[str, tuple[int, str]] = {
    # ---- 大疆 ----
    "Mini 5 Pro": (52, "长续航电池 52 min / 标准 34 min，取长续"),
    "Mini 4 Pro": (34, "长续航电池 45 min / 智能飞行电池 34 min"),
    "Mini 4K": (31, ""),
    "Mini 3": (38, "长续航 51 min / 标准 38 min"),
    "Mini 3 Pro": (34, "长续航 47 min / 标准 34 min"),
    "Mini 2": (31, ""),
    "Mini 2 SE": (31, ""),
    "Mini SE": (30, ""),
    "Mini": (30, ""),
    "Neo": (18, ""),
    "Neo 2": (19, ""),
    "Flip": (34, ""),
    "Air 3S": (45, ""),
    "Air 3": (46, ""),
    "Air 2S": (31, ""),
    "Air 2": (34, ""),
    "Mavic Air": (21, ""),
    "Mavic 4 Pro": (51, ""),
    "Mavic 3 Pro": (43, ""),
    "Mavic 3 Classic": (46, ""),
    "Mavic 3": (46, ""),
    "Mavic 2 Pro": (31, ""),
    "Mavic 2 Zoom": (31, ""),
    "Mavic Pro": (27, ""),
    "Avata 2": (23, ""),
    "Avata": (18, ""),
    "Phantom 4 Pro": (30, ""),
    "Phantom 4": (28, ""),
    "Matrice 4T": (49, ""),
    "Matrice 30T": (41, ""),
    # ---- 道通 ----
    "EVO Lite+": (40, ""),
    "EVO Lite": (40, ""),
    "EVO Nano+": (28, ""),
    "EVO Nano": (28, ""),
    "EVO II Pro": (40, ""),
    "EVO Max 4T": (42, ""),
}


def endurance_min(model: str) -> int | None:
    """机型标称续航（分钟）。查不到返回 None。"""
    m = (model or "").strip()
    for k, (v, _note) in ENDURANCE.items():
        if m.endswith(k) or k in m:
            return v
    return None


def wind_endurance(rated_min: int, wind_ms: float, gust_ms: float = 0.0) -> tuple[int, int]:
    """按当日风估算实际可用续航。返回 (分钟, 采纳风速)。

    原理：悬停功耗随风速上升 —— 机体要前倾对抗风，桨叶工作点偏离
    悬停最优区。行业实测规律大致是：
        - 3 m/s（2 级风）以内几乎无感；
        - 5 m/s（3-4 级）折 15~20%；
        - 8 m/s（5 级）折 30~35%，返航电余量还要另算；
        - 10.8 m/s（6 级）以上多数消费机已到抗风上限，不该飞。
    用二次曲线近似：T = T0 / (1 + 0.04v + 0.009v^2)，v 取阵风与平均
    风速中较不利者 —— 起降和返航都要穿过阵风。下限锁 40%，
    免得曲线在极端风里给出离谱的小数。
    这是估算，不是适航数据；界面上必须带"估算"字样。
    """
    v = max(float(wind_ms or 0), float(gust_ms or 0))
    f = 1 + 0.04 * v + 0.009 * v * v
    t = rated_min / f
    t = max(t, rated_min * 0.40)
    return max(1, round(t)), v


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
_lock = threading.Lock()


def _geocode(city: str, timeout: float = 8.0) -> tuple[float, float] | None:
    """中文地名 -> 经纬度。Open-Meteo geocoding，无 key。"""
    try:
        r = httpx.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "language": "zh", "count": 1},
            timeout=timeout,
            headers={"User-Agent": "uav-reg-watch/1.0"},
        )
        res = (r.json() or {}).get("results") or []
        if not res:
            return None
        return float(res[0]["latitude"]), float(res[0]["longitude"])
    except Exception:              # noqa: BLE001
        return None


def _from_bing(city: str) -> Weather | None:
    """兜底：Bing 搜索结果页抓风级/风速。尽力而为。

    用户说"实在不行用搜索模块" —— 只求比空白强，抓不到就算了。
    """
    try:
        r = httpx.get(
            "https://www.bing.com/search",
            params={"q": city + "天气 风速", "mkt": "zh-CN"},
            timeout=8,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        t = r.text
        # "风速 12 公里/小时" / "12 km/h"
        m = re.search(r"(?:风速|风)[^<]{0,6}(\d{1,2})\s*(?:km/h|公里/小时|公里每小时)", t)
        if m:
            ms = float(m.group(1)) / 3.6
            return Weather(city=city, temp_c=0.0, wind_ms=round(ms, 1),
                           gust_ms=round(ms * 1.5, 1), wind_dir=0, code=2,
                           source="bing",
                           fetched_at=datetime.now().isoformat(timespec="seconds"))
        return None
    except Exception:              # noqa: BLE001
        return None


def get_weather(city: str, data_dir, max_age_min: int = 90):
    """当前天气。先读缓存，过期才出网；出网失败再试 Bing。"""
    if not city:
        return None
    cache = Path(data_dir) / "weather.json"
    with _lock:
        try:
            j = json.loads(cache.read_text(encoding="utf-8"))
            if j.get("city") == city:
                age = datetime.now() - datetime.fromisoformat(j["fetched_at"])
                if age < timedelta(minutes=max_age_min):
                    return Weather(**j)
        except Exception:          # noqa: BLE001
            pass
        w = _fetch_live(city)
        if w is None:
            return None
        try:
            cache.write_text(json.dumps(w.__dict__, ensure_ascii=False),
                             encoding="utf-8")
        except Exception:          # noqa: BLE001
            pass
        return w


def _fetch_live(city: str):
    pos = _geocode(city)
    if pos is None:
        return _from_bing(city)
    lat, lon = pos
    try:
        r = httpx.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "current": "temperature_2m,wind_speed_10m,wind_gusts_10m,"
                           "wind_direction_10m,weather_code",
                "wind_speed_unit": "ms", "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=10,
            headers={"User-Agent": "uav-reg-watch/1.0"},
        )
        c = (r.json() or {}).get("current") or {}
        if "wind_speed_10m" not in c:
            return _from_bing(city)
        return Weather(
            city=city,
            temp_c=float(c.get("temperature_2m") or 0),
            wind_ms=float(c.get("wind_speed_10m") or 0),
            gust_ms=float(c.get("wind_gusts_10m") or 0),
            wind_dir=int(c.get("wind_direction_10m") or 0),
            code=int(c.get("weather_code") or 0),
            source="open-meteo",
            fetched_at=datetime.now().isoformat(timespec="seconds"))
    except Exception:              # noqa: BLE001
        return _from_bing(city)

def brief_lines(city: str, data_dir, devices: list) -> list:
    """晨报用的天气 + 续航要点。拿不到天气返回空列表。

    续航按用户登记的机型算 —— 机型表里没标称续航的机不硬编，
    宁可少一行也不给编出来的数。
    """
    w = get_weather(city, data_dir)
    if w is None:
        return []
    out = [f"{w.text}，{w.temp_c:.0f}℃，{w.dir_text}风 {w.wind_ms:.1f} m/s，"
           f"阵风 {w.gust_ms:.1f} m/s（{w.source}）"]
    v = max(w.wind_ms, w.gust_ms)
    if v >= 10.8:
        out.append("⚠ 阵风已达 6 级以上，多数消费级无人机不建议起飞")
    elif v >= 8.0:
        out.append("⚠ 阵风接近 5 级，小型机谨慎起飞，远离建筑背风面")
    elif v >= 5.5:
        out.append("注意阵风，逆风返航耗电明显增加")
    seen = set()
    for d in devices or []:
        name = d.get("name") or (str(d.get("brand", "")) + " " + str(d.get("model", ""))).strip()
        model = str(d.get("model") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        rated = endurance_min(model)
        if rated is None:
            continue
        est, v_used = wind_endurance(rated, w.wind_ms, w.gust_ms)
        pct = round(est / rated * 100)
        out.append(f"{name}：标称续航 {rated} 分钟，今日风 {v_used:.1f} m/s，"
                   f"估算可用 {est} 分钟（约 {pct}%）")
    return out