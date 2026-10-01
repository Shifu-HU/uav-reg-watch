"""数据源基类。"""

from __future__ import annotations

import logging

from ..fetcher import Fetcher
from ..models import RawItem

log = logging.getLogger("uavwatch.source")


class BaseSource:
    name: str = "base"
    display: str = "base"

    def __init__(self, cfg: dict, fetcher: Fetcher):
        self.cfg = cfg or {}
        self.fetcher = fetcher

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    def fetch(self, since: str = "", full: bool = False) -> list[RawItem]:
        raise NotImplementedError

    def fetch_detail(self, url: str) -> str:
        return ""
