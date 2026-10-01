# -*- coding: utf-8 -*-
"""手机端后端 —— 复用桌面版全部业务逻辑，暴露 HTTP API。

架构：手机(MuMu WebView 壳) -> http://10.0.2.2:8765 -> 本文件 ->
      现有 storage/pipeline/weather/morning/llm 模块。功能零重写。
"""
from __future__ import annotations
import os, sys, threading, re
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import uvicorn

from uavwatch.config import load_config
from uavwatch.storage import Store

cfg = load_config()
store = Store(cfg.db_path)
app = FastAPI(title="无人机法规监控")


@app.get("/", response_class=HTMLResponse)
async def index():
    html = (ROOT / "mobile" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/static/{name}")
async def static_file(name: str):
    p = ROOT / "mobile" / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/stats")
async def stats():
    return store.stats()


@app.get("/api/items")
async def items(page: str = "today", limit: int = 200):
    """与桌面版 _refresh_list 相同的三区分组逻辑。"""
    from datetime import datetime as dt, timedelta as td
    from uavwatch.knowledge import is_no_fly, NoFlyAlert
    if page == "fav":
        rows = store.query(starred=True, limit=limit)
    elif page == "filtered":
        rows = store.foreign(limit=limit)
    else:
        rows = store.query(limit=limit)
    today = dt.now().date()
    d30 = today - td(days=30)
    def _day(r):
        raw = (r.get("published_at") or r.get("first_seen") or "")[:10]
        try: return dt.strptime(raw, "%Y-%m-%d").date()
        except ValueError: return None
    def _window_active(r):
        if not is_no_fly(r): return False
        try:
            a = NoFlyAlert(r, cfg.location.city or "", cfg.location.province or "", today)
        except Exception: return False
        if a.ended: return False
        return (a.active_now or a.start is None or (a.start and a.start >= today))
    major, watch, older = [], [], []
    for r in rows:
        try: imp = int(r.get("importance") or 2)
        except Exception: imp = 2
        if imp == 3: major.append(r); continue
        d = _day(r)
        if (d is None or d >= today or d >= d30 or _window_active(r)):
            watch.append(r)
        else:
            older.append(r)
    # 不再拆块：块内按分级排序（重大 → 重要 → 普通），同级再按日期新→旧
    def _imp(r):
        try: return int(r.get("importance") or 1)
        except Exception: return 1
    def _sort_key(r):
        d = _day(r)
        return (-_imp(r), -(d.toordinal() if d else 0))
    major.sort(key=_sort_key)
    watch.sort(key=_sort_key)
    older.sort(key=_sort_key)
    if page != "today":
        return {"groups": [{"key": "all", "title": "全部", "rows": rows}]}
    return {"groups": [
        {"key": "major", "title": "🚨 重大 · 强制约束", "rows": major},
        {"key": "watch", "title": "⚠️ 近期需注意", "rows": watch},
        {"key": "older", "title": "📚 更早", "rows": older},
    ]}


class ActReq(BaseModel):
    uid: str


@app.post("/api/fav")
async def fav(req: ActReq):
    with store._conn() as c:
        c.execute("UPDATE items SET is_starred = 1 - is_starred WHERE uid=?", (req.uid,))
    return {"ok": True}


@app.post("/api/ignore")
async def ignore(req: ActReq):
    with store._conn() as c:
        c.execute("UPDATE items SET is_ignored = 1 WHERE uid=?", (req.uid,))
    return {"ok": True}


_morning_cache: dict = {}
_morning_gen: dict = {"compact": False, "full": False}
_morning_started: dict = {"compact": 0.0, "full": 0.0}


def _gen_morning(full: bool) -> str:
    from uavwatch.morning import MorningBrief
    brief = MorningBrief(cfg, store, cfg.data_dir)
    if full:
        hp = brief.html_path()
        if hp and hp.exists() and hp.stat().st_size > 200:
            return hp.read_text(encoding="utf-8")
        return brief.render_html(brief.collect())
    return brief.render_html(brief.collect(include_common=False), compact=True)


def _ensure_morning(full: bool) -> None:
    """缓存过期则后台线程生成 —— HTTP 请求永不阻塞。"""
    import time as _t
    key = "full" if full else "compact"
    hit = _morning_cache.get(key)
    if hit and _t.time() - hit[0] < 600:
        return
    if _morning_gen[key]:
        return
    _morning_gen[key] = True
    _morning_started[key] = _t.time()

    def _run():
        try:
            _morning_cache[key] = (_t.time(), _gen_morning(full))
        except Exception as e:
            print("晨报生成失败:", e)
        finally:
            _morning_gen[key] = False
    threading.Thread(target=_run, daemon=True).start()


@app.get("/api/morning/status")
async def morning_status(full: int = 0):
    """前端轮询：ready=true 才去拉 /api/morning。"""
    import time as _t
    key = "full" if full else "compact"
    hit = _morning_cache.get(key)
    ready = bool(hit and _t.time() - hit[0] < 600)
    return {"ready": ready,
            "elapsed": round(_t.time() - _morning_started[key]) if _morning_gen[key] else 0}


@app.get("/api/morning/text")
async def morning_text():
    """纯文本结构化晨报（不用 iframe，WebView 直渲染，带原文链接）。"""
    import time as _t
    hit = _morning_cache.get("compact")
    if not (hit and _t.time() - hit[0] < 600):
        return {"ready": False}
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(hit[1], "html.parser")
    sections = []
    cur = None
    for el in soup.find_all(["h2", "div"]):
        cls = el.get("class") or []
        if el.name == "h2":
            cur = {"title": el.get_text(strip=True), "items": []}
            sections.append(cur)
        elif cur is not None and ("card" in cls or "nf" in cls):
            h3 = el.find("h3")
            # 禁飞块(div.nf)没有 h3 —— 标题在 .where，时段在 .when
            where = el.find(class_="where")
            if not h3 and not where:
                continue
            meta = el.find(class_="meta")
            hint = el.find(class_="hint")
            when = el.find(class_="when")
            a = el.find("a")
            parts = []
            if where:
                parts.append(where.get_text(strip=True))
            if when:
                parts.append(when.get_text(strip=True))
            item = {"title": h3.get_text(strip=True) if h3 else
                            (where.get_text(strip=True) if where else ""),
                    "meta": (" ｜ ".join(parts) if parts else
                             meta.get_text(strip=True) if meta else ""),
                    "hint": hint.get_text(strip=True) if hint else "",
                    "url": a.get("href", "") if a else ""}
            cur["items"].append(item)
    v = soup.find(class_="verdict")
    vmain = v.find(class_="v-main") if v else None
    vsub = v.find(class_="v-sub") if v else None
    return {"ready": True,
            "verdict": {"main": vmain.get_text(strip=True) if vmain else "",
                        "sub": vsub.get_text(strip=True) if vsub else ""},
            "sections": sections}


_jlogs: list = []


@app.post("/api/jslog")
async def jslog(msg: dict):
    """前端 JS 错误上报 —— 现场调试 WebView 渲染问题。"""
    _jlogs.append(str(msg.get("m", ""))[:500])
    if len(_jlogs) > 100:
        del _jlogs[:-100]
    return {"ok": True}


@app.get("/api/jslog")
async def jslog_get():
    return {"logs": _jlogs[-50:]}


@app.get("/api/morning")
async def morning(full: int = 0):
    """晨报 HTML。缓存命中直接回；未命中触发后台生成并回 202。
    前端轮询 /api/morning/status 等 ready。HTTP 永不阻塞 40s。"""
    import time as _t
    key = "full" if full else "compact"
    hit = _morning_cache.get(key)
    if hit and _t.time() - hit[0] < 600:
        return HTMLResponse(hit[1])
    _ensure_morning(full)
    return HTMLResponse("<html><body><!-- generating --></body></html>", status_code=202)


@app.post("/api/morning/prime")
async def morning_prime():
    """后台预热晨报缓存 —— 前端启动即调，用户点开晨报时秒开。"""
    _ensure_morning(False)
    _ensure_morning(True)
    return {"ok": True}
    from uavwatch.weather import brief_lines
    try:
        lines = brief_lines(cfg.location.city, cfg.data_dir, cfg.devices)
    except Exception:
        lines = []
    return {"lines": lines, "city": cfg.location.city or ""}


@app.post("/api/search")
async def search():
    """触发一轮抓取（后台线程，立即返回）。"""
    def _run():
        try:
            from uavwatch.pipeline import Pipeline
            Pipeline(cfg, store).run("manual")
        except Exception as e:
            print("搜索失败:", e)
    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "msg": "已在后台开始搜索"}


_theme_state = {"mode": "dark", "scheme": "薄荷绿·黄绿"}


class ThemeReq(BaseModel):
    mode: str | None = None
    scheme: str | None = None


@app.get("/api/theme")
async def get_theme():
    return _theme_state


@app.get("/api/theme.css")
async def theme_css():
    """用桌面同一套 TH.build_tokens 生成 CSS 变量 —— 设计语言零漂移。"""
    from uavwatch import theme as TH
    t = TH.build_tokens(_theme_state["mode"], _theme_state["scheme"])
    css = ":root{"
    for k, v in t.items():
        css += f"--{k.replace('_', '-')}:{v};"
    # 别名 —— index.html 的语义引用
    css += f"--bg:{t['canvas']};--surface-c:{t['surface']};--raised-c:{t['raised']};"
    css += f"--line-c:{t['line']};--accent-c:{t['accent']};"
    css += f"--must-c:{t['must']};--watch-c:{t['watch']};"
    css += "}"
    return HTMLResponse(css, media_type="text/css")


@app.post("/api/theme")
async def set_theme(req: ThemeReq):
    from uavwatch import theme as TH
    if req.mode:
        _theme_state["mode"] = req.mode if req.mode in ("dark", "light") else "dark"
    if req.scheme and req.scheme in TH.COLOR_SCHEMES:
        _theme_state["scheme"] = req.scheme
    return {"ok": True, **_theme_state}


@app.get("/api/schemes")
async def schemes():
    from uavwatch import theme as TH
    return {"order": TH.SCHEME_ORDER,
            "colors": {k: list(v) for k, v in TH.COLOR_SCHEMES.items()}}


@app.get("/api/status")
async def status():
    return {"city": cfg.location.city or "", "province": cfg.location.province or "",
            "devices": cfg.devices, "version": "mobile-1.0"}


class CityReq(BaseModel):
    city: str = ""
    province: str = ""
    keep_national: bool | None = None
    show_industry: bool | None = None
    daily_time: str | None = None
    model: str | None = None
    add_device: dict | None = None
    remove_device: int | None = None


@app.get("/api/settings")
async def get_settings():
    return {"city": cfg.location.city or "", "province": cfg.location.province or "",
            "keep_national": bool(getattr(cfg.location, "keep_national", True)),
            "show_industry": bool(cfg.raw.get("filter", {}).get("show_industry", False)),
            "daily_time": getattr(cfg.schedule, "daily_time", "07:30"),
            "model": getattr(cfg.llm, "model", ""),
            "models_avail": _ollama_models(),
            "devices": _devices_rich()}




def _devices_rich() -> list[dict]:
    """设备列表 + 重量区间（桌面 CATEGORY_RULES 同源）。"""
    from uavwatch.devices import CATEGORY_RULES
    out = []
    for d in cfg.devices:
        d2 = dict(d)
        lv = d.get("level", "")
        for name, lo, hi in CATEGORY_RULES:
            if name == lv:
                lo_g, hi_g = lo * 1000, hi * 1000
                d2["weight_range"] = (
                    f"{int(lo_g)}g 以上" if hi == float("inf")
                    else f"{int(lo_g)}~{int(hi_g)}g")
                break
        out.append(d2)
    return out


def _ollama_models() -> list[str]:
    try:
        import httpx
        r = httpx.get(getattr(cfg.llm, "host", "http://127.0.0.1:11434") + "/api/tags", timeout=4)
        if r.status_code == 200:
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return []


# 省 → 主要城市（下拉菜单数据源；直辖市/特别行政区单列）
_REGION_RAW = {
    "北京市": "北京市",
    "天津市": "天津市",
    "上海市": "上海市",
    "重庆市": "重庆市",
    "河北省": "石家庄市,唐山市,秦皇岛市,邯郸市,邢台市,保定市,张家口市,承德市,沧州市,廊坊市,衡水市",
    "山西省": "太原市,大同市,阳泉市,长治市,晋城市,朔州市,晋中市,运城市,忻州市,临汾市,吕梁市",
    "内蒙古自治区": "呼和浩特市,包头市,乌海市,赤峰市,通辽市,鄂尔多斯市,呼伦贝尔市,巴彦淖尔市,乌兰察布市,兴安盟,锡林郭勒盟,阿拉善盟",
    "辽宁省": "沈阳市,大连市,鞍山市,抚顺市,本溪市,丹东市,锦州市,营口市,阜新市,辽阳市,盘锦市,铁岭市,朝阳市,葫芦岛市",
    "吉林省": "长春市,吉林市,四平市,辽源市,通化市,白山市,松原市,白城市,延边朝鲜族自治州",
    "黑龙江省": "哈尔滨市,齐齐哈尔市,鸡西市,鹤岗市,双鸭山市,大庆市,伊春市,佳木斯市,七台河市,牡丹江市,黑河市,绥化市,大兴安岭地区",
    "江苏省": "南京市,无锡市,徐州市,常州市,苏州市,南通市,连云港市,淮安市,盐城市,扬州市,镇江市,泰州市,宿迁市",
    "浙江省": "杭州市,宁波市,温州市,嘉兴市,湖州市,绍兴市,金华市,衢州市,舟山市,台州市,丽水市",
    "安徽省": "合肥市,芜湖市,蚌埠市,淮南市,马鞍山市,淮北市,铜陵市,安庆市,黄山市,滁州市,阜阳市,宿州市,六安市,亳州市,池州市,宣城市",
    "福建省": "福州市,厦门市,莆田市,三明市,泉州市,漳州市,南平市,龙岩市,宁德市",
    "江西省": "南昌市,景德镇市,萍乡市,九江市,新余市,鹰潭市,赣州市,吉安市,宜春市,抚州市,上饶市",
    "山东省": "济南市,青岛市,淄博市,枣庄市,东营市,烟台市,潍坊市,济宁市,泰安市,威海市,日照市,临沂市,德州市,聊城市,滨州市,菏泽市",
    "河南省": "郑州市,开封市,洛阳市,平顶山市,安阳市,鹤壁市,新乡市,焦作市,濮阳市,许昌市,漯河市,三门峡市,南阳市,商丘市,信阳市,周口市,驻马店市,济源市",
    "湖北省": "武汉市,黄石市,十堰市,宜昌市,襄阳市,鄂州市,荆门市,孝感市,荆州市,黄冈市,咸宁市,随州市,恩施土家族苗族自治州",
    "湖南省": "长沙市,株洲市,湘潭市,衡阳市,邵阳市,岳阳市,常德市,张家界市,益阳市,郴州市,永州市,怀化市,娄底市,湘西土家族苗族自治州",
    "广东省": "广州市,深圳市,珠海市,汕头市,佛山市,韶关市,湛江市,肇庆市,江门市,茂名市,惠州市,梅州市,汕尾市,河源市,阳江市,清远市,东莞市,中山市,潮州市,揭阳市,云浮市",
    "广西壮族自治区": "南宁市,柳州市,桂林市,梧州市,北海市,防城港市,钦州市,贵港市,玉林市,百色市,贺州市,河池市,来宾市,崇左市",
    "海南省": "海口市,三亚市,三沙市,儋州市",
    "四川省": "成都市,自贡市,攀枝花市,泸州市,德阳市,绵阳市,广元市,遂宁市,内江市,乐山市,南充市,眉山市,宜宾市,广安市,达州市,雅安市,巴中市,资阳市,阿坝藏族羌族自治州,甘孜藏族自治州,凉山彝族自治州",
    "贵州省": "贵阳市,六盘水市,遵义市,安顺市,毕节市,铜仁市,黔西南布依族苗族自治州,黔东南苗族侗族自治州,黔南布依族苗族自治州",
    "云南省": "昆明市,曲靖市,玉溪市,保山市,昭通市,丽江市,普洱市,临沧市,楚雄彝族自治州,红河哈尼族彝族自治州,文山壮族苗族自治州,西双版纳傣族自治州,大理白族自治州,德宏傣族景颇族自治州,怒江傈僳族自治州,迪庆藏族自治州",
    "西藏自治区": "拉萨市,日喀则市,昌都市,林芝市,山南市,那曲市,阿里地区",
    "陕西省": "西安市,铜川市,宝鸡市,咸阳市,渭南市,延安市,汉中市,榆林市,安康市,商洛市",
    "甘肃省": "兰州市,嘉峪关市,金昌市,白银市,天水市,武威市,张掖市,平凉市,酒泉市,庆阳市,定西市,陇南市,临夏回族自治州,甘南藏族自治州",
    "青海省": "西宁市,海东市,海北藏族自治州,黄南藏族自治州,海南藏族自治州,果洛藏族自治州,玉树藏族自治州,海西蒙古族藏族自治州",
    "宁夏回族自治区": "银川市,石嘴山市,吴忠市,固原市,中卫市",
    "新疆维吾尔自治区": "乌鲁木齐市,克拉玛依市,吐鲁番市,哈密市,昌吉回族自治州,博尔塔拉蒙古自治州,巴音郭楞蒙古自治州,阿克苏地区,克孜勒苏柯尔克孜自治州,喀什地区,和田地区,伊犁哈萨克自治州,塔城地区,阿勒泰地区",
    "台湾省": "台北市,新北市,桃园市,台中市,台南市,高雄市,基隆市,新竹市,嘉义市",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}
REGIONS = {k: v.split(",") for k, v in _REGION_RAW.items()}


@app.get("/api/regions")
async def regions():
    """地区下拉数据：省 → 城市列表。"""
    return {"provinces": list(REGIONS.keys()), "cities": REGIONS,
            "city": cfg.location.city or "", "province": cfg.location.province or ""}


@app.post("/api/settings")
async def set_settings(req: CityReq):
    """设置落盘（与桌面同路径 cfg.update）。改城市才触发地区切换全搜。"""
    msgs = []
    if req.city:
        cfg.update("location", city=req.city, province=req.province)
        cfg.location.city = req.city
        cfg.location.province = req.province
        msgs.append(f"地区已改为 {req.city} {req.province}")
    if req.keep_national is not None:
        cfg.update("location", keep_national=req.keep_national)
        cfg.location.keep_national = req.keep_national
        msgs.append("全国性法规：" + ("保留" if req.keep_national else "不保留"))
    if req.show_industry is not None:
        cfg.raw.setdefault("filter", {})["show_industry"] = req.show_industry
        cfg.save()
        msgs.append("商业资讯：" + ("显示" if req.show_industry else "隐藏"))
    if req.daily_time:
        if not re.match(r"^\d{1,2}:\d{2}$", req.daily_time):
            raise HTTPException(400, "时间格式应为 HH:MM")
        cfg.update("schedule", daily_time=req.daily_time)
        try:
            cfg.schedule.daily_time = req.daily_time
        except Exception:
            pass
        msgs.append(f"每日搜索时间 {req.daily_time}")
    if req.model:
        cfg.update("llm", model=req.model)
        try:
            cfg.llm.model = req.model
        except Exception:
            pass
        msgs.append(f"分析模型 {req.model}")
    if req.add_device:
        dev = dict(req.add_device)
        # 机型分级与桌面同规则：克重 → 微型/轻型/小型/…
        if dev.get("grams") and not dev.get("level"):
            try:
                from uavwatch.devices import classify_weight
                dev["level"] = classify_weight(float(dev["grams"]))
            except Exception:
                pass
        try:
            cfg.set_devices(list(cfg.devices) + [dev])
            msgs.append(f"已添加设备 {dev.get('model', '')}"
                        + (f"（{dev.get('level', '')}）" if dev.get("level") else ""))
        except Exception:
            devs = list(cfg.raw.get("devices") or [])
            devs.append(dev)
            cfg.raw["devices"] = devs
            cfg.save()
            msgs.append("已添加设备")
    if req.remove_device is not None:
        try:
            devs = list(cfg.devices)
            if 0 <= req.remove_device < len(devs):
                removed = devs.pop(req.remove_device)
                cfg.set_devices(devs)
                msgs.append(f"已删除设备 {removed.get('model', '')}")
        except Exception as e:
            print("删除设备失败:", e)
    # 换地区才全搜；其它改动（勾选/时间/模型）立即生效不用搜
    _changed_area = bool(req.city) and (
        req.city != (cfg.location.city or "")
        or (req.province or "") != (cfg.location.province or ""))
    if _changed_area:
        def _run():
            try:
                from uavwatch.pipeline import Pipeline
                Pipeline(cfg, store).run("region_switch")
            except Exception as e:
                print("地区切换搜索失败:", e)
        threading.Thread(target=_run, daemon=True).start()
        msgs.append("正在按新地区全面搜索")
    return {"ok": True, "msg": "；".join(msgs) or "没有改动"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")