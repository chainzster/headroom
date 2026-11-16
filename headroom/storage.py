"""SQLite persistence for quota readings and directory scans."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts    INTEGER NOT NULL,
    used  INTEGER NOT NULL,
    total INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS readings_ts ON readings(ts);

CREATE TABLE IF NOT EXISTS dir_scan (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    ts       INTEGER NOT NULL,
    root     TEXT NOT NULL,
    total    INTEGER NOT NULL,
    duration REAL NOT NULL
);
-- Every directory from the latest scan. path is relative to the scan root
-- ("" is the root itself); nchild counts the sub-directories recorded for it.
CREATE TABLE IF NOT EXISTS dir_tree (
    path   TEXT PRIMARY KEY,
    parent TEXT,
    bytes  INTEGER NOT NULL,
    nchild INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS dir_tree_parent ON dir_tree(parent, bytes DESC);
"""

# Before 1.1 only the top level was stored, in dir_sizes. Drop it and the old
# scan header so a fresh multi-level scan runs shortly after upgrading.
MIGRATE = """
DROP TABLE IF EXISTS dir_sizes;
DELETE FROM dir_scan;
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='dir_sizes'").fetchone()
            db.executescript(SCHEMA)
            if legacy:
                db.executescript(MIGRATE)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # -- readings -----------------------------------------------------------

    def add_reading(self, used: int, total: int, ts: Optional[int] = None) -> None:
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO readings (ts, used, total) VALUES (?, ?, ?)",
                       (int(ts or time.time()), used, total))

    def latest_reading(self) -> Optional[Dict]:
        with self._connect() as db:
            row = db.execute(
                "SELECT ts, used, total FROM readings ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return {"ts": row[0], "used": row[1], "total": row[2]} if row else None

    def reading_near(self, ts: int) -> Optional[Dict]:
        """The first reading at or after `ts` (used for change-over-time)."""
        with self._connect() as db:
            row = db.execute(
                "SELECT ts, used, total FROM readings WHERE ts >= ? ORDER BY ts LIMIT 1",
                (ts,),
            ).fetchone()
        return {"ts": row[0], "used": row[1], "total": row[2]} if row else None

    def history(self, since: int, max_points: int = 400) -> List[Tuple[int, int, int]]:
        """Readings since `since`, bucketed so at most ~max_points are returned.

        Each bucket keeps its peak usage, so short spikes stay visible.
        """
        now = int(time.time())
        with self._connect() as db:
            first = db.execute("SELECT MIN(ts) FROM readings WHERE ts >= ?",
                               (since,)).fetchone()[0]
            if first is None:
                return []
            bucket = max(1, (now - first) // max_points + 1)
            rows = db.execute(
                "SELECT MAX(ts), MAX(used), MAX(total) FROM readings WHERE ts >= ? "
                "GROUP BY ts / ? ORDER BY 1",
                (since, bucket),
            ).fetchall()
        return [tuple(r) for r in rows]

    def prune(self, retention_days: int) -> int:
        cutoff = int(time.time()) - retention_days * 86400
        with self._lock, self._connect() as db:
            return db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,)).rowcount

    # -- directory scans ----------------------------------------------------

    def save_scan(self, root: str, total: int, entries: List[Tuple[str, int]],
                  duration: float) -> None:
        """Replace the stored scan. `entries` are (relative path, bytes)."""
        rows = {"": [None, total, 0]}
        for path, size in entries:
            rows[path] = [path.rpartition("/")[0], size, 0]
        for path, row in rows.items():
            if row[0] is not None and row[0] in rows:
                rows[row[0]][2] += 1
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM dir_tree")
            db.executemany(
                "INSERT INTO dir_tree (path, parent, bytes, nchild) VALUES (?, ?, ?, ?)",
                ((p, r[0], r[1], r[2]) for p, r in rows.items()),
            )
            db.execute(
                "INSERT OR REPLACE INTO dir_scan (id, ts, root, total, duration) "
                "VALUES (1, ?, ?, ?, ?)",
                (int(time.time()), root, total, duration),
            )

    def scan_info(self) -> Optional[Dict]:
        with self._connect() as db:
            head = db.execute("SELECT ts, root, total, duration FROM dir_scan").fetchone()
        if not head:
            return None
        return {"ts": head[0], "root": head[1], "total": head[2],
                "duration": round(head[3], 1)}

    def dir_listing(self, path: str, limit: int) -> Optional[Dict]:
        """The largest sub-directories of `path` (relative to the scan root).

        Returns None if `path` wasn't recorded by the latest scan. Space not
        accounted for by sub-directories is reported as `loose` (files
        directly in `path`); sub-directories beyond `limit` are summed into
        `rest`.
        """
        with self._connect() as db:
            node = db.execute("SELECT bytes, nchild FROM dir_tree WHERE path = ?",
                              (path,)).fetchone()
            if not node:
                return None
            rows = db.execute(
                "SELECT path, bytes, nchild FROM dir_tree WHERE parent = ? "
                "ORDER BY bytes DESC LIMIT ?", (path, limit),
            ).fetchall()
            count, child_bytes = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM dir_tree WHERE parent = ?",
                (path,),
            ).fetchone()
        shown = sum(r[1] for r in rows)
        return {
            "path": path,
            "bytes": node[0],
            "directories": [
                {"name": p.rpartition("/")[2], "path": p, "bytes": b, "expandable": n > 0}
                for p, b, n in rows
            ],
            "loose": max(node[0] - child_bytes, 0),
            "rest": {"count": count - len(rows), "bytes": child_bytes - shown},
        }
