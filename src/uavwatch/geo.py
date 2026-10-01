"""地理位置过滤:剔除非用户所在地的法规。

策略(由宽到严):
1. 命中"全国性"特征词 -> 保留, region=全国
2. 命中用户所在城市/省份 -> 保留
3. 命中"also_keep"白名单地区 -> 保留
4. 命中其他明确的地方行政区 -> **剔除**(is_local=False, 记录 region)
5. 无任何地区信号 -> 按保守策略保留(默认判为全国性, 避免漏掉新规)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import REGION_NATIONAL

try:
    from .regions import all_city_names as _all_city_names
    from .regions import find_province as _find_province
except Exception:                         # noqa: BLE001
    _all_city_names = None                # type: ignore[assignment]
    _find_province = None                 # type: ignore[assignment]

# 省级行政区
PROVINCES = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽",
    "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门",
]

# 常见地级市 -> 所属省(覆盖无人机活动密集地区)
CITY_TO_PROVINCE = {
    "深圳": "广东", "广州": "广东", "珠海": "广东", "东莞": "广东", "佛山": "广东",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "义乌": "浙江",
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "徐州": "江苏",
    "成都": "四川", "绵阳": "四川",
    "武汉": "湖北", "宜昌": "湖北", "襄阳": "湖北",
    "西安": "陕西", "咸阳": "陕西",
    "青岛": "山东", "济南": "山东", "烟台": "山东", "潍坊": "山东",
    "厦门": "福建", "福州": "福建", "泉州": "福建",
    "长沙": "湖南", "株洲": "湖南",
    "郑州": "河南", "洛阳": "河南",
    "合肥": "安徽", "芜湖": "安徽",
    "南昌": "江西", "赣州": "江西",
    "昆明": "云南", "大理": "云南",
    "贵阳": "贵州", "遵义": "贵州",
    "南宁": "广西", "桂林": "广西",
    "哈尔滨": "黑龙江", "大庆": "黑龙江",
    "长春": "吉林", "沈阳": "辽宁", "大连": "辽宁",
    "石家庄": "河北", "唐山": "河北", "雄安": "河北",
    "太原": "山西",
    "兰州": "甘肃",
    "银川": "宁夏",
    "西宁": "青海",
    "乌鲁木齐": "新疆", "喀什": "新疆",
    "呼和浩特": "内蒙古", "鄂尔多斯": "内蒙古",
    "拉萨": "西藏",
    "海口": "海南", "三亚": "海南",
    "天津": "天津", "重庆": "重庆",
    # 广东全省 21 个地级市 —— 用户所在地，同省保留判定全靠这张表，
    # 缺一个就意味着该市的规定会被当成"外地"整条丢掉。
    "广州": "广东", "深圳": "广东", "珠海": "广东", "汕头": "广东",
    "佛山": "广东", "韶关": "广东", "湛江": "广东", "肇庆": "广东",
    "江门": "广东", "茂名": "广东", "惠州": "广东", "梅州": "广东",
    "汕尾": "广东", "河源": "广东", "阳江": "广东", "清远": "广东",
    "东莞": "广东", "中山": "广东", "潮州": "广东", "揭阳": "广东",
    "云浮": "广东",
}

# 县级单位里这些是通名，不能当作地区（"开发区人民政府"不是地名）
_GENERIC_COUNTY = ("开发区", "高新区", "新区", "城区", "郊区", "辖区",
                   "自治县", "特区", "林区", "矿区")

# 全国性 / 国家级 信号词 —— 出现即视为适用于所有人
NATIONAL_MARKERS = [
    "中华人民共和国", "国务院", "全国", "国家", "民航局", "中国民用航空局",
    "交通运输部", "工信部", "工业和信息化部", "公安部", "国家空管", "空管委",
    "中央", "国家级", "部令", "总局", "民航规章", "CCAR", "AC-", "咨询通告",
    "适航指令", "国家标准", "行业标准", "MH/T", "GB ", "部委",
]

# 明确的地方性信号词
LOCAL_MARKERS = [
    "管理局", "监管局", "省", "市", "自治区", "地区", "县", "区人民政府",
    "地方", "本地", "本市", "本省",
]

# 民航地区管理局 -> 辖区省份。用户所在省份落在辖区内时保留。
REGIONAL_BUREAUS = {
    "华北地区管理局": {"北京", "天津", "河北", "山西", "内蒙古"},
    "东北地区管理局": {"辽宁", "吉林", "黑龙江"},
    "华东地区管理局": {"上海", "江苏", "浙江", "安徽", "福建", "江西", "山东"},
    "中南地区管理局": {"河南", "湖北", "湖南", "广东", "广西", "海南"},
    "西南地区管理局": {"重庆", "四川", "贵州", "云南", "西藏"},
    "西北地区管理局": {"陕西", "甘肃", "青海", "宁夏", "新疆"},
    "新疆管理局": {"新疆"},
}

# 机场名 -> 所在城市(机场禁飞区公告通常是地方性的)
AIRPORT_CITY = {
    "首都机场": "北京", "大兴机场": "北京", "南苑机场": "北京",
    "浦东机场": "上海", "虹桥机场": "上海",
    "白云机场": "广州", "宝安机场": "深圳",
    "萧山机场": "杭州", "禄口机场": "南京", "双流机场": "成都",
    "天府机场": "成都", "天河机场": "武汉", "咸阳机场": "西安",
    "江北机场": "重庆", "胶东机场": "青岛", "长水机场": "昆明",
}


@dataclass
class GeoResult:
    is_local: bool
    region: str
    scope: str          # national / local
    reason: str


def same_province(city_a: str, city_b: str) -> bool:
    """判断两个地区是否属于同一省级行政区。"""
    pa = city_a if city_a in PROVINCES else CITY_TO_PROVINCE.get(city_a, "")
    pb = city_b if city_b in PROVINCES else CITY_TO_PROVINCE.get(city_b, "")
    return bool(pa and pb and pa == pb)


def _province_of(city: str) -> str:
    """城市 -> 省份。优先用内置小表，其次查完整的省市区数据。"""
    if not city:
        return ""
    if city in PROVINCES:
        return city
    hit = CITY_TO_PROVINCE.get(city)
    if hit:
        return hit
    if _find_province is not None:
        full = _find_province(city)
        if full:
            # 统一成匹配名(去掉"省/市/自治区"后缀)
            for suf in ("维吾尔自治区", "回族自治区", "壮族自治区",
                        "自治区", "省", "市"):
                if full.endswith(suf) and len(full) > len(suf):
                    return full[: -len(suf)]
            return full
    return ""


class GeoFilter:
    def __init__(self, city: str = "", province: str = "",
                 keep_national: bool = True, also_keep: list[str] | None = None,
                 keep_same_province: bool = True):
        self.city = (city or "").strip()
        self.province = (province or "").strip() or _province_of(self.city)
        self.keep_national = keep_national
        # 同省的其他城市规定(如深圳用户看广州)默认保留 —— 省级政策通常全省适用
        self.keep_same_province = keep_same_province
        self.also_keep = [a.strip() for a in (also_keep or []) if a.strip()]
        # 本地可接受地区集合(城市 / 省份 / 白名单及其省份)
        self.local_ok: set[str] = set()
        for x in (self.city, self.province, *self.also_keep):
            if x:
                self.local_ok.add(x)
                p = _province_of(x)
                if p:
                    self.local_ok.add(p)

    # ------------------------------------------------------------------
    def detect_regions(self, text: str) -> list[str]:
        """从文本中抽取出出现的地区名。"""
        found: list[str] = []
        for p in PROVINCES:
            if p in text:
                found.append(p)
        for city in CITY_TO_PROVINCE:
            if city in text and city not in found:
                found.append(city)
        # 完整城市表(336 个地级市) —— 内置小表只覆盖无人机活动密集地区,
        # 其余城市(如"赣州""洛阳")靠这里兜住,否则会被当成"无地区信号"而误留。
        if _all_city_names is not None:
            try:
                for city in _all_city_names():
                    if len(city) >= 2 and city in text and city not in found:
                        found.append(city)
            except Exception:             # noqa: BLE001
                pass
        # 机场 -> 城市
        for ap, city in AIRPORT_CITY.items():
            if ap in text and city not in found:
                found.append(city)
        # 管理局只在没有任何具体地名命中时才作为地区线索。
        # 否则民航局官网全国性法规会因为落款带"中南地区管理局"被误标成广东。
        if not found:
            for bureau, provinces in REGIONAL_BUREAUS.items():
                if bureau in text:
                    my_prov = (self.province if self.province in PROVINCES
                               else CITY_TO_PROVINCE.get(self.city, ""))
                    label = my_prov if (my_prov and my_prov in provinces) else bureau
                    if label not in found:
                        found.append(label)
        return found

    # ------------------------------------------------------------------
    # 标题里的发布机关 —— 地方通告几乎都写成"XX市人民政府关于…"。
    # 这个前缀比正文可靠得多：民航局官网正文常把兄弟单位名单印在
    # 页脚（"广州民航职业技术学院"…），正文扫出来的地区基本是噪声。
    _TITLE_ORG = re.compile(
        r"^[\s【\[（(]*([\u4e00-\u9fa5]{2,6}?(?:省|市|县|区|州|盟))"
        r"(?:人民政府|公安局|公安厅|管理委员会|管委会|办公厅|办公室|"
        r"应急管理局|交通运输局|空管|民航监管局)")

    # 国家级机关站点：正文只作为全国性文件的证据，不拿地区名
    _NATIONAL_HOSTS = ("caac.gov.cn", "www.gov.cn", "mot.gov.cn",
                       "miit.gov.cn", "mps.gov.cn", "ndrc.gov.cn")

    def _locality_from_title(self, title: str) -> str:
        """标题开头的发布机关里点名的地区，取不到返回空串。"""
        m = self._TITLE_ORG.match((title or "").strip())
        if not m:
            return ""
        loc = m.group(1)
        if loc in PROVINCES or loc in CITY_TO_PROVINCE:
            return loc
        if loc.endswith(("省", "市", "州", "盟")):
            bare = loc[:-1]
            if bare in PROVINCES or bare in CITY_TO_PROVINCE:
                return bare
            # 地级市及以上：标题里写出来的基本属实，直接采信。
            # 宁可判成外地（会被剔到"已过滤"页），也不能当成全国性
            # 文件放行 —— 用户报的"秦皇岛市人民政府"混进情报流就是
            # 因为漏判成了全国性。
            return bare or loc
        if loc.endswith(("县", "区")):
            bare = loc[:-1]
            if bare in CITY_TO_PROVINCE:
                return bare
            if bare and bare not in _GENERIC_COUNTY and len(bare) >= 2:
                return bare
        return ""

    def _title_city(self, title: str) -> str:
        """标题里点名的**城市级**地区（不含省级），取不到返回空串。

        只扫标题不扫正文 —— 正文噪声太大（页脚单位名单）。
        机场名也算：AIRPORT_CITY 会把"大兴机场"映射成北京。
        """
        t = title or ""
        for ap, city in AIRPORT_CITY.items():
            if ap in t:
                return city
        for city in CITY_TO_PROVINCE:
            if city in t:
                return city
        # 直辖市（北京/上海/天津/重庆）在 PROVINCES 里而不在城市表，
        # 但它们的"省级"其实就是市级 —— "北京大兴国际机场"必须能
        # 认出北京，否则外地专项文件会被当成全国性放行。
        for m in ("北京", "上海", "天津", "重庆"):
            if m in t:
                return m
        return ""

    @classmethod
    def _is_national_authority(cls, url: str) -> bool:
        if not url:
            return False
        try:
            from urllib.parse import urlparse
            host = (urlparse(url).hostname or "").lower()
        except Exception:                      # noqa: BLE001
            return False
        return any(host == h or host.endswith("." + h)
                   for h in cls._NATIONAL_HOSTS)

    # ------------------------------------------------------------------
    def classify(self, title: str, content: str = "", channel: str = "",
                 url: str = "") -> GeoResult:
        title_loc = self._locality_from_title(title)
        if title_loc:
            # 标题已点名发布地 —— 正文里的国家级词一律不作数，
            # 否则"秦皇岛市人民政府…通告"会因为正文提到"民航局"
            # 被判成全国性文件，从而绕过地区过滤（用户报的问题）。
            regions = [title_loc]
            national_hit = False
        elif self._is_national_authority(url):
            # 民航局 / 中国政府网发的文件。正文页脚的兄弟单位名单会把
            # 它们误标成"广州"等地区（实测 4 条 CAAC 文件中招），
            # 所以不扫正文 —— 但**标题**里点名的外地城市要认：
            # "关于公布北京大兴国际机场障碍物限制面保护范围的公告"
            # 是北京的事，对深圳飞手没有约束力（用户报"北京大兴机场"）。
            loc = self._title_city(title)
            if loc and loc not in self.local_ok:
                return GeoResult(False, loc, "local",
                                 f"国家级机关发布的{loc}专项文件，与所在地无关")
            return GeoResult(True, REGION_NATIONAL, "national",
                             "国家级机关发布")
        else:
            text = f"{title} {channel} {content[:1500]}"
            regions = self.detect_regions(text)
            national_hit = any(m in text for m in NATIONAL_MARKERS)

        # --- 无用户定位: 只留全国性 ---
        if not self.city and not self.province:
            if regions and not national_hit:
                return GeoResult(False, regions[0], "local",
                                 f"未设置所在地，且命中地区 {regions[0]}")
            return GeoResult(True, REGION_NATIONAL, "national", "未设置所在地，默认保留全国性")

        local_hit = [r for r in regions if r in self.local_ok]

        # --- 命中本地 -> 保留 ---
        if local_hit:
            return GeoResult(True, local_hit[0], "local", f"命中所在地 {local_hit[0]}")

        # --- 同省 -> 只保留"省级"文件 ---
        #
        # 原来只要同省就留，于是"高考期间江门部分区域临时禁飞管控"
        # 这类**别的城市的具体通告**也进了情报流（用户报"你看看江门"）。
        # 同省保留的初衷是"省级政策全省适用"，那就只留省级。
        if self.keep_same_province and self.province:
            same = [r for r in regions
                    if r not in self.local_ok and same_province(r, self.province)]
            prov_level = [r for r in same if r in PROVINCES]
            if prov_level:
                return GeoResult(True, prov_level[0], "local",
                                 f"同省({self.province})省级规定: {prov_level[0]}")

        # --- 命中外地 -> 剔除 ---
        foreign = [r for r in regions if r not in self.local_ok]
        if foreign and not (national_hit and len(regions) > 1):
            return GeoResult(False, foreign[0], "local",
                             f"命中外地 {foreign[0]}，与所在地 {self.city or self.province} 无关")

        # --- 全国性 -> 保留 ---
        if national_hit or not regions:
            return GeoResult(True, REGION_NATIONAL, "national", "全国性文件")

        # --- 有地区但都不是本地,且不明确全国性 ---
        if foreign:
            return GeoResult(False, foreign[0], "local", f"地方性文件({foreign[0]})，非所在地")

        return GeoResult(True, REGION_NATIONAL, "national", "无地区信号，保守保留")

    # ------------------------------------------------------------------
    def filter_items(self, items: list) -> tuple[list, list]:
        """返回 (保留, 剔除)。就地写入 is_local/region/region_scope。"""
        kept, dropped = [], []
        for it in items:
            r = self.classify(it.title, getattr(it, "content", "") or "",
                              getattr(it, "channel", "") or getattr(it, "source_name", ""),
                              getattr(it, "url", "") or "")
            if hasattr(it, "region"):
                it.region = r.region
                it.region_scope = r.scope
                it.is_local = r.is_local
            (kept if r.is_local else dropped).append(it)
        return kept, dropped
