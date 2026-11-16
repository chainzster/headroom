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
CREATE TABLE IF NOT EXISTS dir_sizes (
    name  TEXT NOT NULL,
    bytes INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

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

    def save_scan(self, root: str, total: int, dirs: List[Tuple[str, int]],
                  duration: float) -> None:
        with self._lock, self._connect() as db:
            db.execute("DELETE FROM dir_sizes")
            db.executemany("INSERT INTO dir_sizes (name, bytes) VALUES (?, ?)", dirs)
            db.execute(
                "INSERT OR REPLACE INTO dir_scan (id, ts, root, total, duration) "
                "VALUES (1, ?, ?, ?, ?)",
                (int(time.time()), root, total, duration),
            )

    def latest_scan(self, limit: int) -> Optional[Dict]:
        with self._connect() as db:
            head = db.execute("SELECT ts, root, total, duration FROM dir_scan").fetchone()
            if not head:
                return None
            rows = db.execute(
                "SELECT name, bytes FROM dir_sizes ORDER BY bytes DESC LIMIT ?", (limit,)
            ).fetchall()
        return {
            "ts": head[0], "root": head[1], "total": head[2],
            "duration": round(head[3], 1),
            "directories": [{"name": n, "bytes": b} for n, b in rows],
        }
