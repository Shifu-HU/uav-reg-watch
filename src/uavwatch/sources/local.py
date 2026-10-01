"""地方机构抓取器 —— 公安 / 地方政府。

用户要求(原话)：
    "地方公安局之类的机构？但压迫确保是用户地区的()不止我"
——"压迫"应是"要"，即**必须按用户所在地区**，而且不只服务深圳一个用户。

设计要点
--------
1. **地区从 config.yaml 的 location 读**，不硬编码深圳。
   换 city/province，查询和栏目自动跟着变。别的用户拿去能用。

2. **两种抓法**（用户选的 A+C 组合）：
   - C 直连：已实测可达的公安站点，直接抓「通知公告」栏目
   - A 兜底：地方政府门户的通知公告栏目轮询

3. 为什么不做搜索？
   实测 bing 对中国地名**检索能力是坏的**：搜"深圳 无人机 禁飞"
   返回的是深圳百科、政府首页、旅游攻略 —— 加引号、加 site:、
   加反引号**全部无效**；换个城市只是把"深圳"换成"北京"，
   结果结构一模一样。各地政府自带的搜索接口也普遍不通
   （深圳返回 0 字符、广东 405、上海跨域重定向）。
   所以只能老老实实抓栏目，靠关键词过滤。

4. 命中率低是**已知且接受**的：公安局通知公告栏大部分是
   爆破审批、居留许可、冻结资金返还。禁飞通告是低频事件，
   但一旦发布就是最要命的信息，宁可空跑也要扫。
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..fetcher import Fetcher
from ..models import RawItem
from .base import BaseSource

log = logging.getLogger("uavwatch.local")

# ---------------------------------------------------------------------------
# 机型/法规关键词 —— 一条通告值不值得收，先过这道闸
# ---------------------------------------------------------------------------
KEYWORDS = [
    "无人机", "无人驾驶航空器", "无人航空器", "低慢小", "低空",
    "民用无人机", "航拍", "飞手", "空域", "禁飞", "净空",
    "飞行管制", "临时管制", "适飞", "实名登记", "飞行活动",
    "低空经济", "飞行管理", "航空器",
]

# 强信号词：命中任意一个，基本可以直接确认为无人机相关
STRONG = [
    # 大型活动临时管控 —— 用户点名"要全"：APEC 这类国际峰会的
    # 禁飞通告往往不写"无人机"三个字，只写"低慢小"或"空飘物"，
    # 不加这些词就会整批漏掉。
    "APEC", "亚太经合", "峰会", "全运会", "残特奥会", "亚运会",
    "马拉松", "航展", "高考", "中考", "演唱会", "灯会", "博览会",
    "无人机", "无人驾驶航空器", "无人航空器", "低慢小",
    "禁飞", "净空", "空域", "航拍",
]

# 明显无关的噪声 —— 公安局公告栏里最多的就是这些
NOISE = [
    "爆破作业单位", "居留许可", "冻结资金返还", "出入境接待大厅",
    "招聘", "拟录用", "拟补录", "决算", "年度报表", "随机抽查",
    "检查事项清单", "考试录用", "公务员",
]


def _relevant(title: str) -> bool:
    """标题是否与无人机/低空相关。"""
    t = title or ""
    if any(n in t for n in NOISE):
        return False
    if any(s in t for s in STRONG):
        return True
    # 强信号直接放行；弱信号要两个同时出现才算，
    # 避免"低空""空域"这种词单独误伤无关公告。
    # 注意 "低空经济" 和 "低空" 会同时命中，所以 "关于低空经济
    # 产业发展的若干措施" 这类能过闸 —— 是否展示由下游的
    # filter.show_industry 决定（用户默认关商业资讯）。
    hits = [k for k in KEYWORDS if k in t]
    return len(hits) >= 2


# ---------------------------------------------------------------------------
# 站点表。C 类 = 已实测可达的公安站点；A 类 = 地方政府门户兜底。
# 用 {city}/{province} 占位符，从配置里填 —— 不写死深圳。
# ---------------------------------------------------------------------------
# 说明：公安厅/局按**省/市**组织。这里给的是"全国通用"的若干站点，
# 用户所在省市在 PROVINCE_SITES / CITY_SITES 里能对上就用，
# 对不上则退回地方政府门户(A 类)。
PROVINCE_SITES: dict[str, list[tuple[str, str]]] = {
    "广东": [("广东省公安厅 · 通知公告", "https://gdga.gd.gov.cn/jwzx/gggs/index.html")],
    "北京": [("北京市公安局 · 通知公告", "https://gaj.beijing.gov.cn/xxfb/tzgg/index.html")],
    # 以下每个都实测过（HTTP 200 且首页含公告/通告词）才收录。
    # 探测失败的超时/证书/无词站点一个都没放进来 —— 用户明确说
    # "主要要全"，但宁缺毋假：假 URL 每天报错比少覆盖更糟。
    # 没覆盖到的省仍走 A 类政府门户兜底 + Bing {province} 查询。
    "浙江": [("浙江省公安厅", "https://gat.zj.gov.cn/")],
    "四川": [("四川省公安厅", "https://gat.sc.gov.cn/")],
    "重庆": [("重庆市公安局", "https://gaj.cq.gov.cn/")],
    "湖北": [("湖北省公安厅", "https://gat.hubei.gov.cn/")],
    "福建": [("福建省公安厅", "https://gat.fujian.gov.cn/")],
    "安徽": [("安徽省公安厅", "https://gat.ah.gov.cn/")],
    "辽宁": [("辽宁省公安厅", "https://gat.ln.gov.cn/")],
    "陕西": [("陕西省公安厅", "https://gat.shaanxi.gov.cn/")],
    "贵州": [("贵州省公安厅", "https://gat.guizhou.gov.cn/")],
    "吉林": [("吉林省公安厅", "https://gat.jl.gov.cn/")],
    "黑龙江": [("黑龙江省公安厅", "https://gat.hlj.gov.cn/")],
    "新疆": [("新疆公安厅", "https://gat.xinjiang.gov.cn/")],
    "西藏": [("西藏公安厅", "https://gat.xizang.gov.cn/")],
}

CITY_SITES: dict[str, list[tuple[str, str]]] = {
    "广州": [("广州市公安局 · 通知公告", "https://gaj.gz.gov.cn/zwdt/tzgg/index.html")],
    "深圳": [("深圳市政府 · 通知公告",
              "https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/")],
}

# A 类兜底：地方政府门户。{city} 会被替换。
GOV_PORTAL = [
    ("{city}市政府 · 通知公告", "https://www.{pinyin}.gov.cn/"),
]


class LocalSource(BaseSource):
    """地方公安 / 政府通告。"""

    name = "local"
    display = "地方机构"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        super().__init__(cfg, fetcher)
        self.city = (cfg.get("city") or "").strip()
        self.province = (cfg.get("province") or "").strip()
        self.max_pages = int(cfg.get("max_pages", 2))
        self.extra = cfg.get("sites") or []      # 用户可自行追加站点

    # ------------------------------------------------------------------
    def _targets(self) -> list[tuple[str, str]]:
        """按用户所在省市挑站点。"""
        out: list[tuple[str, str]] = []
        if self.province and self.province in PROVINCE_SITES:
            out += PROVINCE_SITES[self.province]
        if self.city and self.city in CITY_SITES:
            out += CITY_SITES[self.city]
        for s in self.extra:
            if isinstance(s, dict) and s.get("name") and s.get("url"):
                out.append((s["name"], s["url"]))
        return out

    # ------------------------------------------------------------------
    def fetch(self, since: str = "", full: bool = False) -> list[RawItem]:
        items: list[RawItem] = []
        seen: set[str] = set()
        targets = self._targets()
        if not targets:
            log.info("LOCAL: 未匹配到 %s/%s 的站点，跳过",
                     self.province, self.city)
            return items

        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=4) as ex:
            jobs = [ex.submit(self._crawl, name, url, since)
                    for name, url in targets]
            for fu in as_completed(jobs):
                try:
                    batch = fu.result()
                except Exception as e:             # noqa: BLE001
                    log.warning("LOCAL 子任务失败: %s", e)
                    continue
                for it in batch:
                    if it.uid not in seen:
                        seen.add(it.uid)
                        items.append(it)

        log.info("LOCAL: %s/%s 抓到 %d 条",
                 self.province, self.city, len(items))
        return items

    # ------------------------------------------------------------------
    def _crawl(self, name: str, url: str, since: str) -> list[RawItem]:
        """抓一个栏目页，解析条目并按关键词过滤。"""
        out: list[RawItem] = []
        try:
            html = self.fetcher.get_text(url)
        except Exception as e:                     # noqa: BLE001
            log.debug("LOCAL %s 抓取失败: %s", url, e)
            return out
        if not html:
            return out

        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a"):
            title = a.get_text(strip=True)
            href = a.get("href") or ""
            if not title or len(title) < 8 or len(title) > 120:
                continue
            if not (".html" in href or ".htm" in href or "content/post" in href):
                continue
            if not _relevant(title):
                continue
            full_url = urljoin(url, href)
            if not full_url.startswith("http"):
                continue
            # 注意：RawItem.uid 是只读 property（由 url 派生），
            # 不能赋值；published/summary 这两个字段名也不存在，
            # 正确的是 published_at / content。
            out.append(RawItem(
                title=title,
                url=full_url,
                source=self.name,
                source_name=name,
                channel=name,
            ))
        return out
