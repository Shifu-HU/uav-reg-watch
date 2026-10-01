"""SQLite 持久化 + 增量状态哨兵。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import RegItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    uid           TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    source        TEXT NOT NULL,
    source_name   TEXT DEFAULT '',
    published_at  TEXT DEFAULT '',
    effective_at  TEXT DEFAULT '',
    content       TEXT DEFAULT '',
    fetch_excerpt TEXT DEFAULT '',
    ai_summary    TEXT DEFAULT '',
    category      TEXT DEFAULT '其他',
    importance    INTEGER DEFAULT 2,
    region        TEXT DEFAULT '全国',
    region_scope  TEXT DEFAULT 'national',
    is_local      INTEGER DEFAULT 1,
    ai_processed  INTEGER DEFAULT 0,
    content_hash  TEXT DEFAULT '',
    first_seen    TEXT NOT NULL,
    is_read       INTEGER DEFAULT 0,
    is_starred    INTEGER DEFAULT 0,
    is_ignored    INTEGER DEFAULT 0,
    pushed_at     TEXT DEFAULT '',
    -- 适用设备类别，逗号分隔。空或"通用"表示与重量级无关。
    -- 由 devices.applicable_categories() 标注，按用户设备过滤。
    device_cats   TEXT DEFAULT '',
    -- 法规要点，由 regcheck.RegChecker 用本地小模型抽取
    key_action    TEXT DEFAULT '',
    key_penalty   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_items_published  ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_category   ON items(category);
CREATE INDEX IF NOT EXISTS idx_items_region     ON items(region);
CREATE INDEX IF NOT EXISTS idx_items_hash       ON items(content_hash);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,        -- bootstrap / daily / manual
    started_at  TEXT NOT NULL,
    finished_at TEXT DEFAULT '',
    fetched     INTEGER DEFAULT 0,
    new_items   INTEGER DEFAULT 0,
    kept_local  INTEGER DEFAULT 0,
    filtered    INTEGER DEFAULT 0,
    pushed      INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'running',
    note        TEXT DEFAULT ''
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)
            # WAL: 允许进度查看器在抓取过程中实时读到已提交的数据
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        """给已有库补新列。

        CREATE TABLE IF NOT EXISTS 不会给**已存在**的表加列，
        所以新增字段必须显式 ALTER。漏了这步，老用户升级后
        一查询就 "no such column"。
        """
        want = {
            "device_cats": "TEXT DEFAULT ''",
            "key_action": "TEXT DEFAULT ''",
            "key_penalty": "TEXT DEFAULT ''",
        }
        have = {r["name"] for r in c.execute("PRAGMA table_info(items)")}
        for col, decl in want.items():
            if col not in have:
                c.execute(f"ALTER TABLE items ADD COLUMN {col} {decl}")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------------- meta 哨兵 ----------------
    def get_meta(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    # ---------------- 首次搜索哨兵 ----------------
    @property
    def bootstrap_done(self) -> bool:
        return self.get_meta("bootstrap_done") == "true"

    def mark_bootstrap_done(self, items_seen: int) -> None:
        self.set_meta("bootstrap_done", "true")
        self.set_meta("bootstrap_at", datetime.now().isoformat(timespec="seconds"))
        self.set_meta("bootstrap_items", str(items_seen))

    @property
    def last_search_at(self) -> str:
        return self.get_meta("last_search_at", "")

    def touch_last_search(self) -> None:
        self.set_meta("last_search_at", datetime.now().isoformat(timespec="seconds"))

    # ---------------- 条目 ----------------
    def exists_uid(self, uid: str) -> bool:
        with self._conn() as c:
            return c.execute("SELECT 1 FROM items WHERE uid=?", (uid,)).fetchone() is not None

    def exists_hash(self, content_hash: str) -> bool:
        if not content_hash:
            return False
        with self._conn() as c:
            return c.execute(
                "SELECT 1 FROM items WHERE content_hash=? LIMIT 1", (content_hash,)
            ).fetchone() is not None

    def upsert_items(self, items: list[RegItem]) -> int:
        """插入新条目，返回真正新插入的数量。"""
        if not items:
            return 0
        inserted = 0
        with self._conn() as c:
            for it in items:
                cur = c.execute(
                    """INSERT OR IGNORE INTO items
                    (uid,title,url,source,source_name,published_at,effective_at,content,
                     fetch_excerpt,ai_summary,category,importance,region,region_scope,
                     is_local,ai_processed,content_hash,first_seen,pushed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        it.uid, it.title, it.url, it.source, it.source_name,
                        it.published_at, it.effective_at, it.content, it.fetch_excerpt,
                        it.ai_summary, it.category, it.importance, it.region,
                        it.region_scope, int(it.is_local), int(it.ai_processed),
                        it.content_hash, it.first_seen, it.pushed_at,
                    ),
                )
                inserted += cur.rowcount
        return inserted

    def update_item_ai(self, uid: str, summary: str, category: str,
                       importance: int, region: str, region_scope: str,
                       is_local: bool) -> None:
        with self._conn() as c:
            c.execute(
                """UPDATE items SET ai_summary=?, category=?, importance=?, region=?,
                   region_scope=?, is_local=?, ai_processed=1 WHERE uid=?""",
                (summary, category, importance, region, region_scope,
                 int(is_local), uid),
            )

    def annotate(self, uid: str, *, category: str = "",
                 device_cats: str = "", key_action: str = "",
                 key_penalty: str = "") -> None:
        """回填分类 / 设备类别 / 法规要点。只写非空值。"""
        sets, vals = [], []
        for col, v in (("category", category), ("device_cats", device_cats),
                       ("key_action", key_action), ("key_penalty", key_penalty)):
            if v:
                sets.append(f"{col}=?")
                vals.append(v)
        if not sets:
            return
        vals.append(uid)
        with self._conn() as c:
            c.execute(f"UPDATE items SET {','.join(sets)} WHERE uid=?", tuple(vals))

    def query_by_device(self, my_cats: list[str], *, limit: int = 500,
                        **kw) -> list[dict[str, Any]]:
        """按设备类别取条目。通用条目永远返回。

        过滤放在 Python 侧而不是 SQL —— device_cats 是逗号分隔的
        多值字段，SQL 里做子串匹配容易出 "小型" 命中 "中型" 这类
        边界错误（虽然这里不会，但没必要冒这个险）。
        """
        from .devices import matches_device, GENERIC
        rows = self.query(limit=limit, **kw)
        if not my_cats:
            return rows
        out = []
        for r in rows:
            raw = (r.get("device_cats") or "").strip()
            cats = [x for x in raw.split(",") if x] if raw else [GENERIC]
            if matches_device(cats, my_cats):
                out.append(r)
        return out

    def pending_ai(self, limit: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM items WHERE ai_processed=0 ORDER BY first_seen DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql).fetchall()]

    def query(self, *, since: str = "", category: str = "", region: str = "",
              only_local: bool = True, include_ignored: bool = False,
              starred: bool = False, search: str = "", limit: int = 500,
              offset: int = 0) -> list[dict[str, Any]]:
        where, args = [], []
        if since:
            where.append("first_seen >= ?"); args.append(since)
        if category:
            where.append("category = ?"); args.append(category)
        if region:
            where.append("region = ?"); args.append(region)
        if only_local:
            where.append("is_local = 1")
        if not include_ignored:
            where.append("is_ignored = 0")
        if starred:
            where.append("is_starred = 1")
        if search:
            where.append("(title LIKE ? OR ai_summary LIKE ? OR content LIKE ?)")
            like = f"%{search}%"; args += [like, like, like]
        sql = "SELECT * FROM items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(NULLIF(published_at,''), first_seen) DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def foreign(self, limit: int = 500) -> list[dict[str, Any]]:
        """被地理过滤规则剔除的条目(仅外地)。

        界面用它展示「为什么这条没推给我」，让过滤结果可解释、可追溯。
        """
        sql = ("SELECT * FROM items WHERE is_local = 0 AND is_ignored = 0 "
               "ORDER BY COALESCE(NULLIF(published_at,''), first_seen) DESC LIMIT ?")
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, (limit,)).fetchall()]

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        """最近的运行记录。"""
        try:
            with self._conn() as c:
                rows = c.execute(
                    "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:                 # noqa: BLE001
            return []

    def unread_pool(self, limit: int = 300, min_importance: int = 1,
                    only_local: bool = True) -> list[dict[str, Any]]:
        """未读情报池: 用于补足每日推送量。

        每天真正的新规通常远少于 200 条，因此每日推送 = 当日新增 + 历史未读重要条目。
        这样既保证每日情报量达标，又不会重复推送已读内容。
        """
        where = ["is_read = 0", "is_ignored = 0", "importance >= ?"]
        args: list[Any] = [min_importance]
        if only_local:
            where.append("is_local = 1")
        sql = ("SELECT * FROM items WHERE " + " AND ".join(where)
               + " ORDER BY importance DESC, "
                 "COALESCE(NULLIF(published_at,''), first_seen) DESC LIMIT ?")
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def mark_read(self, uids: list[str]) -> None:
        if not uids:
            return
        with self._conn() as c:
            c.executemany("UPDATE items SET is_read=1 WHERE uid=?", [(u,) for u in uids])

    def mark(self, uid: str, **flags: bool) -> None:
        allowed = {"is_read", "is_starred", "is_ignored"}
        sets = [f"{k}=?" for k in flags if k in allowed]
        vals = [int(v) for k, v in flags.items() if k in allowed]
        if not sets:
            return
        with self._conn() as c:
            c.execute(f"UPDATE items SET {','.join(sets)} WHERE uid=?", (*vals, uid))

    def demote_third_party(self, keep_ratio: float = 0.10,
                           dry_run: bool = True) -> dict:
        """把超配额的第三方来源条目标记为"已忽略"。

        用户要求以民航局官网为主、第三方约 10%。新条目由
        Pipeline.limit_third_party 把关；这里处理**存量**。

        用 is_ignored 而不是删除 —— 数据还在，随时可恢复，
        也不会因为误判而永久丢信息。官方源一条不动。

        返回 {"official": n, "third": n, "demoted": n, "kept": n}
        """
        try:
            from .verify import classify_trust, TRUST_OFFICIAL
        except Exception:                      # noqa: BLE001
            return {"official": 0, "third": 0, "demoted": 0, "kept": 0,
                    "error": "verify 模块不可用"}

        rows = self.query(only_local=False, include_ignored=False, limit=5000)
        official, third = [], []
        for r in rows:
            if classify_trust(r.get("url") or "") == TRUST_OFFICIAL:
                official.append(r)
            else:
                third.append(r)

        if keep_ratio >= 1.0 or not third:
            return {"official": len(official), "third": len(third),
                    "demoted": 0, "kept": len(third)}

        allow = (int(len(official) * keep_ratio / (1.0 - keep_ratio))
                 if keep_ratio > 0 else 0)
        # 只在官方很少时兜底，别把库清空；官方充足时严格按比例
        if len(official) < 15:
            allow = max(allow, min(len(third), 15))

        # 保留优先级：重要度高、本地、有生效日期的先留
        third.sort(key=lambda r: (
            -int(r.get("importance") or 1),
            0 if r.get("is_local") else 1,
            0 if r.get("effective_at") else 1,
        ))
        to_demote = third[allow:]
        uids = [r["uid"] for r in to_demote]

        if not dry_run and uids:
            with self._conn() as c:
                c.executemany(
                    "UPDATE items SET is_ignored=1 WHERE uid=?",
                    [(u,) for u in uids])

        return {"official": len(official), "third": len(third),
                "demoted": len(uids), "kept": allow}

    def mark_pushed(self, uids: list[str]) -> None:
        if not uids:
            return
        now = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.executemany("UPDATE items SET pushed_at=? WHERE uid=?", [(now, u) for u in uids])

    # ---------------- 统计 ----------------
    def stats(self) -> dict[str, Any]:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
            local = c.execute("SELECT COUNT(*) n FROM items WHERE is_local=1").fetchone()["n"]
            pending = c.execute("SELECT COUNT(*) n FROM items WHERE ai_processed=0").fetchone()["n"]
            today = datetime.now().date().isoformat()
            today_n = c.execute(
                "SELECT COUNT(*) n FROM items WHERE substr(first_seen,1,10)=?", (today,)
            ).fetchone()["n"]
            today_pushed = c.execute(
                "SELECT COUNT(*) n FROM items WHERE substr(pushed_at,1,10)=?", (today,)
            ).fetchone()["n"]
            by_cat = {
                r["category"]: r["n"]
                for r in c.execute(
                    "SELECT category, COUNT(*) n FROM items GROUP BY category ORDER BY n DESC"
                ).fetchall()
            }
            by_region = {
                r["region"]: r["n"]
                for r in c.execute(
                    "SELECT region, COUNT(*) n FROM items GROUP BY region ORDER BY n DESC LIMIT 20"
                ).fetchall()
            }
        return {
            "total": total, "local": local, "filtered": total - local,
            "pending_ai": pending, "today_new": today_n, "today_pushed": today_pushed,
            "by_category": by_cat, "by_region": by_region,
        }

    # ---------------- 运行记录 ----------------
    def start_run(self, kind: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO runs(kind,started_at) VALUES(?,?)",
                (kind, datetime.now().isoformat(timespec="seconds")),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, **kw: Any) -> None:
        fields = ["finished_at", "fetched", "new_items", "kept_local",
                  "filtered", "pushed", "status", "note"]
        sets, vals = ["finished_at=?"], [datetime.now().isoformat(timespec="seconds")]
        for f in fields[1:]:
            if f in kw:
                sets.append(f"{f}=?"); vals.append(kw[f])
        with self._conn() as c:
            c.execute(f"UPDATE runs SET {','.join(sets)} WHERE id=?", (*vals, run_id))

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()]

    def known_urls(self) -> set[str]:
        with self._conn() as c:
            return {r["url"] for r in c.execute("SELECT url FROM items").fetchall()}
