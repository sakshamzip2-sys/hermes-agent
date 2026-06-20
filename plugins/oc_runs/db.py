"""SQLite-backed durable spine for the unified run-event log (``oc_runs.db``).

Two tables: ``run_events`` is the append-only log whose ``seq`` is a global,
monotonic, durable id (emitted in the SSE ``id:`` field and used as the
``Last-Event-ID`` resume cursor); ``run_snapshots`` is a pure-fold projection
cache that is always rebuildable by replaying ``run_events`` and is therefore
never a second source of truth.

This DB is a GLOBAL registry (keyed off the default hermes root, like
``oc_agents.db``), not a per-profile store, because the cockpit shows all runs
across profiles. ``HERMES_OC_RUNS_DB`` overrides the path (drainer handoff,
tests). Append is idempotent via ``UNIQUE(run_id, dedupe_key)`` so a re-emitted
event (two reconcilers, a drainer replay after a crash) collapses to one row.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .events import SCHEMA_VERSION

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_events (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version INTEGER NOT NULL,
    ts             REAL NOT NULL,
    run_id         TEXT NOT NULL,
    parent_run_id  TEXT,
    source         TEXT NOT NULL,
    type           TEXT NOT NULL,
    agent_id       TEXT,
    team_id        TEXT,
    payload_json   TEXT,
    dedupe_key     TEXT
);

-- Idempotent emit: a non-null dedupe_key collapses re-emits within a run_id.
-- NULL dedupe_key rows are distinct (SQLite treats NULLs as unequal), so
-- progress/heartbeat events without a key always append.
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_events_dedupe
    ON run_events(run_id, dedupe_key);
CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, seq);

-- Pure-fold projection cache. Rebuildable from run_events at any time.
CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id         TEXT PRIMARY KEY,
    last_seq       INTEGER NOT NULL,
    state_json     TEXT,
    schema_version INTEGER NOT NULL,
    updated_at     REAL NOT NULL
);
"""

_local = threading.local()


def db_path() -> Path:
    override = os.environ.get("HERMES_OC_RUNS_DB", "").strip()
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from hermes_constants import get_default_hermes_root

        root = Path(get_default_hermes_root())
    except Exception:
        root = Path(os.path.expanduser("~/.hermes"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "oc_runs.db"


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    path = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is None or getattr(_local, "path", None) != path:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn = sqlite3.connect(path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        _local.conn = conn
        _local.path = path
    yield conn


def _now() -> float:
    return time.time()


def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "seq": int(row["seq"]),
        "schema_version": int(row["schema_version"]),
        "ts": float(row["ts"]),
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "source": row["source"],
        "type": row["type"],
        "agent_id": row["agent_id"],
        "team_id": row["team_id"],
        "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
        "dedupe_key": row["dedupe_key"],
    }


def append_event(event: Dict[str, Any]) -> int:
    """Append one event and return its durable seq.

    Idempotent: if an event with the same ``(run_id, dedupe_key)`` already
    exists, no new row is written and the existing seq is returned. Events with
    no ``dedupe_key`` always insert. This is the single seq-assigning writer.
    """
    run_id = event["run_id"]
    dedupe_key = event.get("dedupe_key")
    with connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO run_events
               (schema_version, ts, run_id, parent_run_id, source, type,
                agent_id, team_id, payload_json, dedupe_key)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                int(event.get("schema_version", SCHEMA_VERSION)),
                float(event.get("ts") or _now()),
                run_id,
                event.get("parent_run_id"),
                event["source"],
                event["type"],
                event.get("agent_id"),
                event.get("team_id"),
                json.dumps(event.get("payload") or {}),
                dedupe_key,
            ),
        )
        if cur.rowcount == 0:
            # Deduped on UNIQUE(run_id, dedupe_key): return the existing seq.
            row = conn.execute(
                "SELECT seq FROM run_events WHERE run_id=? AND dedupe_key IS ?",
                (run_id, dedupe_key),
            ).fetchone()
            conn.commit()
            return int(row["seq"]) if row else 0
        conn.commit()
        return int(cur.lastrowid or 0)


def tail_since(seq: int, limit: int = 1000) -> List[Dict[str, Any]]:
    """Return up to ``limit`` events with seq > ``seq``, oldest-first. This is
    the resume read for the SSE tail (``Last-Event-ID``) and the projection."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE seq > ? ORDER BY seq LIMIT ?",
            (int(seq), int(limit)),
        ).fetchall()
        return [_row_to_event(r) for r in rows]


def events_for_run(run_id: str, after_seq: int = 0) -> List[Dict[str, Any]]:
    """All events for one run with seq > ``after_seq`` (for the per-run fold)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_events WHERE run_id=? AND seq>? ORDER BY seq",
            (run_id, int(after_seq)),
        ).fetchall()
        return [_row_to_event(r) for r in rows]


def latest_seq() -> int:
    with connect() as conn:
        row = conn.execute("SELECT MAX(seq) m FROM run_events").fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0


def upsert_snapshot(run_id: str, *, last_seq: int, state: Dict[str, Any]) -> None:
    """Write/replace the fold cache for one run. Never a source of truth."""
    with connect() as conn:
        conn.execute(
            """INSERT INTO run_snapshots (run_id, last_seq, state_json, schema_version, updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET
                   last_seq=excluded.last_seq,
                   state_json=excluded.state_json,
                   schema_version=excluded.schema_version,
                   updated_at=excluded.updated_at""",
            (run_id, int(last_seq), json.dumps(state or {}), SCHEMA_VERSION, _now()),
        )
        conn.commit()


def _row_to_snapshot(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "last_seq": int(row["last_seq"]),
        "state": json.loads(row["state_json"]) if row["state_json"] else {},
        "schema_version": int(row["schema_version"]),
        "updated_at": float(row["updated_at"]),
    }


def get_snapshot(run_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
        return _row_to_snapshot(row) if row else None


def list_snapshots() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM run_snapshots ORDER BY updated_at DESC"
        ).fetchall()
        return [_row_to_snapshot(r) for r in rows]
