"""SQLite store for the orchestrator control plane (``oc_orchestrator.db``).

Tables:
  - ``run_leases``: per run-tree caps, remaining budget, and a monotonic spawn
    backstop counter. One row per top-level goal.
  - ``slot_reservations``: the atomic concurrency/budget ledger. A reserved row
    is one live worker slot; releasing it frees concurrency. The counted
    resource and the lock are the SAME row in the SAME DB, so two concurrent
    admits cannot both pass the cap check (the cross-DB cap race the council
    flagged is closed by construction).
  - ``spawn_intents`` + ``recovery_claims``: intent-then-execute recovery, so a
    crash between deciding to spawn and actually spawning is reconcilable
    (neither double-spawn nor abandon).
  - ``driver_lease``: the leader-leased fencing token (one logical driver).
  - ``orchestrator_decisions``: an audit trail of routing/recovery/cap actions.

``HERMES_OC_ORCHESTRATOR_DB`` overrides the path (tests, worker handoff). Global
registry like the other parallel-agents DBs (keyed off the default hermes root).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_leases (
    run_tree_id            TEXT PRIMARY KEY,
    max_depth              INTEGER NOT NULL,
    max_workers_per_parent INTEGER NOT NULL,
    max_concurrent         INTEGER NOT NULL,
    max_fanout             INTEGER NOT NULL,
    max_spawns             INTEGER NOT NULL,
    budget_usd             REAL,          -- remaining budget; NULL = unbounded
    spawns_total           INTEGER NOT NULL DEFAULT 0,  -- monotonic backstop
    created_at             REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS slot_reservations (
    id           TEXT PRIMARY KEY,
    run_tree_id  TEXT NOT NULL,
    parent_node  TEXT,
    depth        INTEGER NOT NULL,
    est_usd      REAL NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'reserved',  -- reserved|released
    created_at   REAL NOT NULL,
    released_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_slot_res_tree ON slot_reservations(run_tree_id, status);

CREATE TABLE IF NOT EXISTS spawn_intents (
    id             TEXT PRIMARY KEY,
    run_tree_id    TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    failure_seq    INTEGER,
    reservation_id TEXT,
    attempt_no     INTEGER NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',  -- pending|launched|done|abandoned
    child_id       TEXT,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spawn_intents_state ON spawn_intents(state, run_tree_id);

CREATE TABLE IF NOT EXISTS recovery_claims (
    run_tree_id TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    failure_seq INTEGER NOT NULL,
    attempt_no  INTEGER NOT NULL,
    claimed_at  REAL NOT NULL,
    PRIMARY KEY (run_tree_id, task_id, failure_seq)
);

CREATE TABLE IF NOT EXISTS driver_lease (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    holder        TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    expires_at    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orchestrator_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    run_tree_id TEXT,
    kind        TEXT NOT NULL,
    detail_json TEXT
);
"""

_local = threading.local()


def db_path() -> Path:
    override = os.environ.get("HERMES_OC_ORCHESTRATOR_DB", "").strip()
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
    return root / "oc_orchestrator.db"


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
        # Autocommit mode so we control transactions explicitly with
        # BEGIN IMMEDIATE / COMMIT / ROLLBACK for the compare-and-swap ledger.
        conn.isolation_level = None
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(SCHEMA_SQL)
        _local.conn = conn
        _local.path = path
    yield conn


def now() -> float:
    return time.time()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def record_decision(conn, run_tree_id: str, kind: str, detail: dict) -> None:
    conn.execute(
        "INSERT INTO orchestrator_decisions (ts, run_tree_id, kind, detail_json) VALUES (?,?,?,?)",
        (now(), run_tree_id, kind, json.dumps(detail or {})),
    )
