"""Durable event store for proactivity.

SQLite at ``$HERMES_HOME/proactivity/proactivity.db`` holding tracked events and
their lifecycle state. CAS-style state transitions keep the loop idempotent.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from .models import EventState, TrackedEvent

_COLUMNS = (
    "id", "title", "starts_at", "ends_at", "source", "url", "sensitivity",
    "attended_confirmed", "state", "surfaced_at", "pushed_at", "acked_at", "created_at",
)

# Fully literal INSERT (no string interpolation) — all values bound as parameters.
_INSERT_SQL = (
    "INSERT OR REPLACE INTO events "
    "(id, title, starts_at, ends_at, source, url, sensitivity, attended_confirmed, "
    "state, surfaced_at, pushed_at, acked_at, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)



class ProactivityStore:
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
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    starts_at REAL NOT NULL,
                    ends_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    url TEXT,
                    sensitivity TEXT NOT NULL,
                    attended_confirmed INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    surfaced_at REAL,
                    pushed_at REAL,
                    acked_at REAL,
                    created_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_state ON events(state)")

    # -- writes -------------------------------------------------------------

    def add_event(self, ev: TrackedEvent) -> None:
        row = ev.to_row()
        with self._connect() as conn:
            conn.execute(_INSERT_SQL, tuple(row[c] for c in _COLUMNS))

    # All mutations use fully literal SQL with bound parameters — no dynamic
    # statement composition anywhere in this store.
    def set_state(self, event_id: str, state: EventState) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE events SET state = ? WHERE id = ?", (state.value, event_id))

    def mark_surfaced(self, event_id: str, ts: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET state = ?, surfaced_at = ? WHERE id = ?",
                (EventState.SURFACED.value, ts, event_id),
            )

    def mark_pushed(self, event_id: str, ts: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET state = ?, pushed_at = ? WHERE id = ?",
                (EventState.PUSHED.value, ts, event_id),
            )

    def mark_acked(self, event_id: str, ts: float) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET state = ?, acked_at = ? WHERE id = ?",
                (EventState.ACKED.value, ts, event_id),
            )

    def mark_expired(self, event_id: str) -> None:
        self.set_state(event_id, EventState.EXPIRED)

    def set_attended(self, event_id: str, attended: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE events SET attended_confirmed = ? WHERE id = ?",
                (int(attended), event_id),
            )

    def delete(self, event_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))

    # -- reads --------------------------------------------------------------

    def get(self, event_id: str) -> Optional[TrackedEvent]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return TrackedEvent.from_row(dict(row)) if row else None

    def all_events(self) -> list[TrackedEvent]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events ORDER BY ends_at DESC").fetchall()
        return [TrackedEvent.from_row(dict(r)) for r in rows]

    def by_state(self, *states: EventState) -> list[TrackedEvent]:
        if not states:
            return []
        # One literal single-state query per requested state, merged + de-duped,
        # so no dynamic IN-clause is ever composed.
        out: list[TrackedEvent] = []
        seen: set[str] = set()
        with self._connect() as conn:
            for s in states:
                rows = conn.execute(
                    "SELECT * FROM events WHERE state = ? ORDER BY ends_at ASC",
                    (s.value,),
                ).fetchall()
                for r in rows:
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        out.append(TrackedEvent.from_row(dict(r)))
        out.sort(key=lambda e: e.ends_at)
        return out

    def promote_ended_to_pending(self, now: float) -> list[TrackedEvent]:
        """TRACKED events whose end time has passed become PENDING (check-in owed)."""
        promoted: list[TrackedEvent] = []
        for ev in self.by_state(EventState.TRACKED):
            if ev.ends_at <= now:
                self.set_state(ev.id, EventState.PENDING)
                ev.state = EventState.PENDING
                promoted.append(ev)
        return promoted

    def pending(self) -> list[TrackedEvent]:
        return self.by_state(EventState.PENDING)

    def surfaced_or_pushed(self) -> list[TrackedEvent]:
        """Events awaiting a user reply (already shown), newest first."""
        evs = self.by_state(EventState.SURFACED, EventState.PUSHED)
        evs.sort(key=lambda e: (e.surfaced_at or e.pushed_at or 0.0), reverse=True)
        return evs

    def pushes_since(self, since_ts: float) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM events WHERE pushed_at IS NOT NULL AND pushed_at >= ?",
                (since_ts,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def counts_by_state(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT state, COUNT(*) FROM events GROUP BY state").fetchall()
        return {r[0]: int(r[1]) for r in rows}
