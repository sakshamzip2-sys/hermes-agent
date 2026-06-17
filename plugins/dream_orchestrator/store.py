"""Durable ledger + global lock for the unified-dreaming orchestrator.

A small SQLite database under ``$HERMES_HOME/dreaming/orchestrator.db`` (next to
the local dreamer's ``dreaming.db``) holding:

- ``runs``      — one row per orchestrated ``dream-all`` run keyed by a
                  ``dream_run_id``, with the JSON outcome per target. Makes runs
                  inspectable (``dream-all status``) and idempotent.
- ``imported``  — Phase-2 idempotency ledger: a stable hash per imported/derived
                  candidate line, so the same upstream conclusion/fact is never
                  re-imported on a later run (and so the local dreamer can exclude
                  it from its own candidate pool — the no-recursion invariant).
- ``meta``      — key/value scalars (last-run id/ts, a coarse run lock).

Stdlib ``sqlite3`` only — works identically in CLI and gateway processes.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

# A run is considered "in progress" (lock held) for at most this long. A crashed
# run never wedges the lock forever — a later run reclaims a stale lock.
_LOCK_TTL_SECONDS = 30 * 60.0


class OrchestratorStore:
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
                "CREATE TABLE IF NOT EXISTS runs ("
                "dream_run_id TEXT PRIMARY KEY, started_at REAL NOT NULL, "
                "finished_at REAL, summary TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS imported ("
                "import_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
                "ref TEXT, ts REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
            )

    # -- run ledger ---------------------------------------------------------

    @staticmethod
    def new_run_id() -> str:
        return "dr-" + uuid.uuid4().hex[:12]

    def record_run(self, dream_run_id: str, summary: dict, *,
                   started_at: float, finished_at: Optional[float] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs(dream_run_id, started_at, finished_at, summary) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(dream_run_id) DO UPDATE SET "
                "finished_at = excluded.finished_at, summary = excluded.summary",
                (dream_run_id, started_at, finished_at, json.dumps(summary)),
            )

    def last_run(self) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT dream_run_id, started_at, finished_at, summary "
                "FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        try:
            summary = json.loads(row[3])
        except (TypeError, ValueError):
            summary = {}
        return {
            "dream_run_id": row[0],
            "started_at": row[1],
            "finished_at": row[2],
            "summary": summary,
        }

    def recent_runs(self, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dream_run_id, started_at, finished_at, summary "
                "FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            try:
                summary = json.loads(r[3])
            except (TypeError, ValueError):
                summary = {}
            out.append({
                "dream_run_id": r[0], "started_at": r[1],
                "finished_at": r[2], "summary": summary,
            })
        return out

    # -- coarse global lock (prevents concurrent runs) ----------------------

    def acquire_lock(self, dream_run_id: str) -> bool:
        """Atomically take the run lock. False if another run holds a fresh lock."""
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'run_lock'"
            ).fetchone()
            if row and row[0]:
                try:
                    held = json.loads(row[0])
                    if now - float(held.get("ts", 0)) < _LOCK_TTL_SECONDS:
                        return False  # a fresh lock is held by another run
                except (TypeError, ValueError):
                    pass  # corrupt lock -> reclaim
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('run_lock', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (json.dumps({"id": dream_run_id, "ts": now}),),
            )
        return True

    def release_lock(self, dream_run_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'run_lock'"
            ).fetchone()
            if not row or not row[0]:
                return
            try:
                held = json.loads(row[0])
            except (TypeError, ValueError):
                held = {}
            if held.get("id") == dream_run_id:
                conn.execute("DELETE FROM meta WHERE key = 'run_lock'")

    # -- Phase-2 import ledger ----------------------------------------------

    def is_imported(self, import_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM imported WHERE import_id = ?", (import_id,)
            ).fetchone()
        return row is not None

    def imported_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT import_id FROM imported").fetchall()
        return {r[0] for r in rows}

    def mark_imported(self, import_id: str, *, source: str, ref: str = "") -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO imported(import_id, source, ref, ts) "
                "VALUES (?, ?, ?, ?)",
                (import_id, source, ref, time.time()),
            )
