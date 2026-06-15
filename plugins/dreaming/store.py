"""Durable state for the dreaming plugin.

A small SQLite database under ``$HERMES_HOME/dreaming/dreaming.db`` holding:

- ``processed`` — event-id idempotency ledger so a fact is never evaluated twice.
- ``meta``      — key/value scalars (last-run timestamp, etc.).
- ``audit``     — one row per dreaming pass with the outcome counts, for
                  ``hermes dream status`` and post-hoc debugging.

Kept deliberately tiny and dependency-free (stdlib ``sqlite3`` only) so it works
identically in CLI and gateway processes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Optional


class DreamStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS processed ("
                "event_id TEXT PRIMARY KEY, ts REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                "summary TEXT NOT NULL)"
            )

    # -- idempotency ledger -------------------------------------------------

    def processed_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_id FROM processed").fetchall()
        return {r[0] for r in rows}

    def mark_processed(self, event_ids: list[str]) -> None:
        if not event_ids:
            return
        now = time.time()
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO processed(event_id, ts) VALUES (?, ?)",
                [(eid, now) for eid in event_ids],
            )

    # -- scalar metadata ----------------------------------------------------

    def get_meta(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def last_run_ts(self) -> float:
        raw = self.get_meta("last_run_ts")
        try:
            return float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    def set_last_run_ts(self, ts: float) -> None:
        self.set_meta("last_run_ts", repr(float(ts)))

    # -- audit --------------------------------------------------------------

    def record_run(self, summary: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit(ts, summary) VALUES (?, ?)",
                (time.time(), json.dumps(summary)),
            )

    def recent_runs(self, limit: int = 10) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, summary FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out: list[dict] = []
        for ts, summary in rows:
            try:
                parsed = json.loads(summary)
            except (TypeError, ValueError):
                parsed = {}
            parsed["ts"] = ts
            out.append(parsed)
        return out
