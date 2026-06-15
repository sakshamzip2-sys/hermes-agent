"""Persistence for proactive moments + the shared notification-budget ledger.

SQLite at ``$HERMES_HOME/proactivity/moments.db``. Moments are keyed by ``dedup_key`` so
a source re-emitting the same candidate across polls upserts rather than duplicates. The
``sends`` ledger is the cross-source notification budget (every push records here, so all
sources together can't exceed the cap).

All SQL is fully literal with bound parameters — no dynamic statement composition.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .moment import MomentState, ProactiveMoment

_INSERT = (
    "INSERT INTO moments (dedup_key, id, source_id, category, title, body, reasoning, "
    "trigger_at, expires_at, urgency, sensitivity, confidence, suggested_action, state, "
    "created_at, surfaced_at, delivered_at, acked_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(dedup_key) DO UPDATE SET "
    "body=excluded.body, reasoning=excluded.reasoning, trigger_at=excluded.trigger_at, "
    "expires_at=excluded.expires_at, urgency=excluded.urgency, confidence=excluded.confidence"
)


class MomentStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS moments (
                    dedup_key TEXT PRIMARY KEY,
                    id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    reasoning TEXT,
                    trigger_at REAL,
                    expires_at REAL,
                    urgency REAL,
                    sensitivity TEXT NOT NULL,
                    confidence REAL,
                    suggested_action TEXT,
                    state TEXT NOT NULL,
                    created_at REAL,
                    surfaced_at REAL,
                    delivered_at REAL,
                    acked_at REAL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_moments_state ON moments(state)")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sends (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ts REAL NOT NULL, channel TEXT NOT NULL)"
            )

    # -- moments ------------------------------------------------------------

    def upsert(self, m: ProactiveMoment) -> bool:
        """Insert a moment, or refresh a still-pending duplicate. Returns True if this
        is a newly-seen moment (first time we've recorded this dedup_key)."""
        key = m.ensure_dedup_key()
        existing = self.get(key)
        r = m.to_row()
        with self._connect() as conn:
            conn.execute(_INSERT, (
                r["dedup_key"], r["id"], r["source_id"], r["category"], r["title"],
                r["body"], r["reasoning"], r["trigger_at"], r["expires_at"], r["urgency"],
                r["sensitivity"], r["confidence"], r["suggested_action"], r["state"],
                r["created_at"], r["surfaced_at"], r["delivered_at"], r["acked_at"],
            ))
        return existing is None

    def get(self, dedup_key: str) -> Optional[ProactiveMoment]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM moments WHERE dedup_key = ?", (dedup_key,)).fetchone()
        return ProactiveMoment.from_row(dict(row)) if row else None

    def by_state(self, state: MomentState) -> list[ProactiveMoment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM moments WHERE state = ? ORDER BY urgency DESC, created_at ASC",
                (state.value,),
            ).fetchall()
        return [ProactiveMoment.from_row(dict(r)) for r in rows]

    def pending(self) -> list[ProactiveMoment]:
        return self.by_state(MomentState.PENDING)

    def digest_queue(self) -> list[ProactiveMoment]:
        return self.by_state(MomentState.DIGEST)

    def awaiting_reply(self) -> list[ProactiveMoment]:
        """Moments surfaced/delivered and not yet acked (newest first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM moments WHERE state IN ('surfaced', 'delivered') "
                "ORDER BY COALESCE(surfaced_at, delivered_at, 0) DESC"
            ).fetchall()
        return [ProactiveMoment.from_row(dict(r)) for r in rows]

    def set_state(self, dedup_key: str, state: MomentState, *, surfaced_at: Optional[float] = None,
                  delivered_at: Optional[float] = None, acked_at: Optional[float] = None) -> None:
        cur = self.get(dedup_key)
        if cur is None:
            return
        s_at = surfaced_at if surfaced_at is not None else cur.surfaced_at
        d_at = delivered_at if delivered_at is not None else cur.delivered_at
        a_at = acked_at if acked_at is not None else cur.acked_at
        with self._connect() as conn:
            conn.execute(
                "UPDATE moments SET state = ?, surfaced_at = ?, delivered_at = ?, acked_at = ? "
                "WHERE dedup_key = ?",
                (state.value, s_at, d_at, a_at, dedup_key),
            )

    def expire_stale(self, now: float) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE moments SET state = 'expired' WHERE state IN ('pending', 'digest') "
                "AND expires_at > 0 AND expires_at < ?",
                (now,),
            )
            return cur.rowcount

    def all_moments(self, limit: int = 50) -> list[ProactiveMoment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM moments ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [ProactiveMoment.from_row(dict(r)) for r in rows]

    # -- notification budget ledger ----------------------------------------

    def record_send(self, ts: float, channel: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO sends(ts, channel) VALUES (?, ?)", (ts, channel))

    def pushes_since(self, since_ts: float) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sends WHERE channel = 'push' AND ts >= ?", (since_ts,)
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def counts_by_state(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state, COUNT(*) FROM moments GROUP BY state").fetchall()
        return {r[0]: int(r[1]) for r in rows}
