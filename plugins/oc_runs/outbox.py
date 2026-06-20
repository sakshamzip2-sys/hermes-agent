"""The transactional outbox each engine writes into its own DB.

An engine (oc_agents, oc_teams, oc_flow) calls ``ensure_outbox(conn)`` in its
connect path and ``enqueue(conn, event)`` in the SAME transaction as its own
state mutation. That atomicity is the whole point: the event is durably queued
exactly when (and only when) the state change commits, so a crash cannot leave
state changed but the event lost, nor an event emitted for a change that rolled
back. A single drainer (see ``drainer.py``) later moves undrained rows into the
spine and marks them drained.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from .events import SCHEMA_VERSION

OUTBOX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_outbox (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             REAL NOT NULL,
    run_id         TEXT NOT NULL,
    parent_run_id  TEXT,
    source         TEXT NOT NULL,
    type           TEXT NOT NULL,
    agent_id       TEXT,
    team_id        TEXT,
    payload_json   TEXT,
    dedupe_key     TEXT,
    schema_version INTEGER NOT NULL DEFAULT 1,
    drained        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_run_outbox_undrained ON run_outbox(drained, id);
"""


def ensure_outbox(conn) -> None:
    """Create the run_outbox table on this connection if absent. Cheap and
    idempotent; safe to call on every connect (mirrors the engine schema path)."""
    conn.executescript(OUTBOX_SCHEMA_SQL)


def enqueue(conn, event: Dict[str, Any]) -> int:
    """Insert one outbox row from an event envelope (see events.build_event).
    Does NOT commit: the caller commits in the same transaction as its state
    change. Returns the new outbox row id."""
    cur = conn.execute(
        """INSERT INTO run_outbox
           (ts, run_id, parent_run_id, source, type, agent_id, team_id,
            payload_json, dedupe_key, schema_version, drained)
           VALUES (?,?,?,?,?,?,?,?,?,?,0)""",
        (
            float(event.get("ts") or time.time()),
            event["run_id"],
            event.get("parent_run_id"),
            event["source"],
            event["type"],
            event.get("agent_id"),
            event.get("team_id"),
            json.dumps(event.get("payload") or {}),
            event.get("dedupe_key"),
            int(event.get("schema_version", SCHEMA_VERSION)),
        ),
    )
    return int(cur.lastrowid or 0)


def fetch_undrained(conn, limit: int = 500) -> List[Dict[str, Any]]:
    """Undrained outbox rows oldest-first, each as a dict carrying outbox_id."""
    rows = conn.execute(
        "SELECT * FROM run_outbox WHERE drained=0 ORDER BY id LIMIT ?",
        (int(limit),),
    ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        out.append({
            "outbox_id": int(r["id"]),
            "ts": float(r["ts"]),
            "run_id": r["run_id"],
            "parent_run_id": r["parent_run_id"],
            "source": r["source"],
            "type": r["type"],
            "agent_id": r["agent_id"],
            "team_id": r["team_id"],
            "payload": json.loads(r["payload_json"]) if r["payload_json"] else {},
            "dedupe_key": r["dedupe_key"],
            "schema_version": int(r["schema_version"]),
        })
    return out


def mark_drained(conn, ids: List[int]) -> None:
    """Mark the given outbox rows drained. Caller commits."""
    if not ids:
        return
    conn.executemany(
        "UPDATE run_outbox SET drained=1 WHERE id=?",
        [(int(i),) for i in ids],
    )
