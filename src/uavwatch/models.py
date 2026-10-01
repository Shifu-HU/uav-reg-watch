"""领域模型。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any


REGION_NATIONAL = "全国"


@dataclass
class RawItem:
    """抓取到的原始条目(未经 AI 处理)。"""

    title: str
    url: str
    source: str              # caac / searxng / bing
    source_name: str = ""    # 人类可读的来源名，如"民航局 · 通知公告"
    published_at: str = ""   # ISO8601 字符串，可能为空
    content: str = ""        # 正文(可能只有摘要)
    channel: str = ""        # 频道/栏目
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def uid(self) -> str:
        """稳定唯一 id: 优先用 URL，退化到标题。"""
        key = self.url.strip() or self.title.strip()
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]

    @property
    def content_hash(self) -> str:
        body = (self.content or self.title).strip()
        # 去掉常见空白差异，避免同一内容因排版不同而误判为新
        norm = "".join(body.split())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegItem:
    """经过去重、地理过滤、AI 处理后的法规条目。"""

    uid: str
    title: str
    url: str
    source: str
    source_name: str = ""
    published_at: str = ""
    effective_at: str = ""            # 生效日期
    content: str = ""
    fetch_excerpt: str = ""           # 抓取到的原文摘要
    ai_summary: str = ""              # 本地模型生成的摘要
    category: str = "其他"             # 空域管理/飞行审批/实名登记/驾驶员资质/禁飞区/处罚/其他
    importance: int = 2               # 1=一般 2=重要 3=重大
    region: str = REGION_NATIONAL     # 所属地区
    region_scope: str = "national"    # national / local
    is_local: bool = True             # 是否与用户所在地相关
    ai_processed: bool = False
    content_hash: str = ""
    first_seen: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    is_read: bool = False
    is_starred: bool = False
    is_ignored: bool = False
    pushed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 主题分类关键词表(用于无模型时的规则兜底 + 给模型做少样本提示)
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "空域管理": ["空域", "飞行管制", "航线", "低空空域", "空中交通", "管制区"],
    "飞行审批": ["审批", "申请", "许可", "报备", "批准", "飞行计划", "作业许可"],
    "实名登记": ["实名", "登记", "注册", "国籍", "标识", "编码", "UOM"],
    "驾驶员资质": ["驾驶员", "执照", "合格证", "资质", "培训", "考试", "操控员", "教员"],
    "禁飞区": ["禁飞", "限飞", "净空", "禁飞区", "限制区", "临时禁飞", "机场净空"],
    "处罚案例": ["处罚", "罚款", "违法", "查处", "案例", "没收", "警告", "追责"],
    "适航认证": ["适航", "型号合格", "适航证", "生产许可", "TC", "PC", "AC"],
    "产业政策": ["低空经济", "产业", "补贴", "规划", "发展", "试点", "示范"],
    "运行管理": ["运行", "运营", "作业", "物流", "植保", "巡检", "编队", "表演"],
}
