"""批量标注：分类修正 + 设备类别 + 法规要点。

把三件互相独立的事收在一处，因为它们的输入相同（标题 + 正文）、
输出都写回同一批记录，分散实现会重复读库三遍。

哪些跑模型、哪些跑规则，是按**可靠性**分的，不是按方便：
    分类修正     规则     标题主导词很稳定，模型反而会飘
    设备类别     规则     纯字符串匹配，模型做纯属浪费还可能出错
    法规要点     模型     需要读懂条文语义，规则做不到
"""

from __future__ import annotations

import logging

from .devices import applicable_categories, label_for
from .regcheck import RegChecker, fix_category

log = logging.getLogger("uavwatch.annotate")


def annotate_rows(store, rows: list[dict], llm=None, *,
                  llm_limit: int = 0, progress=None) -> dict:
    """给一批记录打标注。

    llm_limit > 0 时才跑模型（抽取要点），0 表示只跑规则部分。
    返回统计，供调用方展示"改了 X 条分类 / 标了 Y 条设备"。
    """
    stat = {"scanned": 0, "recat": 0, "device": 0, "keys": 0, "failed": 0}

    checker = RegChecker(llm) if (llm is not None and llm_limit > 0) else None
    budget = llm_limit

    for r in rows:
        title = r.get("title") or ""
        body = (r.get("content") or r.get("fetch_excerpt") or "")
        old_cat = r.get("category") or ""
        stat["scanned"] += 1

        # 1) 分类修正 —— 纯规则
        new_cat = fix_category(title, body, old_cat)
        if new_cat and new_cat != old_cat:
            stat["recat"] += 1

        # 2) 设备类别 —— 纯规则
        cats = applicable_categories(title + " " + body[:600])
        dev = label_for(cats)
        if dev:
            stat["device"] += 1

        # 3) 法规要点 —— 用模型，受预算限制
        act = pen = ""
        if checker is not None and budget > 0 and _worth_check(title, body):
            budget -= 1
            res = checker.extract(title, body)
            if res:
                act = res.get("action", "")
                pen = res.get("penalty", "")
                if act or pen:
                    stat["keys"] += 1

        try:
            store.annotate(r["uid"], category=new_cat, device_cats=dev,
                           key_action=act, key_penalty=pen)
        except Exception as e:                 # noqa: BLE001
            stat["failed"] += 1
            log.debug("标注写回失败 %s: %s", r.get("uid"), e)

        if progress and stat["scanned"] % 20 == 0:
            progress(stat["scanned"], len(rows))

    return stat


def _worth_check(title: str, body: str) -> bool:
    """值不值得花模型算力。

    禁飞通告由 llmcheck 单独处理，这里跳过避免重复；
    内容太短说明没抓到正文，抽不出东西。
    """
    t = title or ""
    import re
    if re.search(r"禁飞|限飞|临时管控|从严管控|管控通告", t):
        return False
    if len(body) < 80:
        return False
    return True
