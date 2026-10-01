"""Dedup: URL exact + title similarity + content fingerprint."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_PUNCT = re.compile(r"[\s\-_\u2014\u2013\u00b7\u3001\uff0c\u3002\uff1b\uff1a\uff01\uff1f\u201c\u201d\u2018\u2019\uff08\uff09\u300a\u300b\u3010\u3011()<>\[\]|/\\,.;:!?~`']+")
_TRIM = re.compile(r"^(\u5173\u4e8e|\u5370\u53d1|\u53d1\u5e03|\u516c\u5e03|\u89e3\u8bfb|\u4e00\u56fe\u8bfb\u61c2|\u901a\u77e5|\u516c\u544a|\u901a\u544a)+")


def norm_title(t: str) -> str:
    t = _PUNCT.sub("", t or "")
    t = _TRIM.sub("", t)
    return t


def similar(a: str, b: str) -> float:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # containment means same doc appearing in two channels
    if len(a) > 8 and len(b) > 8 and (a in b or b in a):
        return 0.95
    return SequenceMatcher(None, a, b).ratio()


class Deduper:
    def __init__(self, threshold: float = 0.86):
        self.threshold = threshold

    def dedup(self, items: list, existing_titles: list[str] | None = None) -> list:
        kept: list = []
        titles: list[str] = list(existing_titles or [])
        hashes: set[str] = set()

        for it in items:
            h = getattr(it, "content_hash", "")
            if h and h in hashes:
                continue
            dup = False
            for t in titles:
                if similar(it.title, t) >= self.threshold:
                    dup = True
                    break
            if dup:
                continue
            kept.append(it)
            titles.append(it.title)
            if h:
                hashes.add(h)
        return kept
