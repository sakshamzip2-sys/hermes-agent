"""SQLite-backed persistence for the oc_flow dynamic-workflow runtime.

A *flow run* is one execution of an orchestration script. The runtime records
the run, its phases, and every subagent (``flow_agents``) it spawns, so that:

* ``hermes flow list`` / ``hermes flow show`` can report on past + live runs
  without the runtime holding them in memory,
* a run can be **resumed**: completed agent calls return their cached result
  instantly (keyed by call index + a hash of the prompt), only new/edited
  calls run live — the same property Claude Code's dynamic workflows have,
* a **background** run (detached worker subprocess) can be observed from any
  other ``hermes`` invocation, since the DB is the shared source of truth.

The DB is standalone (``<root>/oc_flow.db``), mirroring ``kanban_db.py`` — we
never touch the core ``hermes_state`` schema. ``HERMES_OC_FLOW_DB`` overrides
the path (used by tests and by the dispatcher→worker handoff so a detached
worker resolves the exact same file).
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
from typing import Any, Dict, Generator, List, Optional

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS flow_runs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT,
    script_path   TEXT,
    script_sha    TEXT,
    args_json     TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending|running|completed|failed|stopped
    background    INTEGER NOT NULL DEFAULT 0,
    pid           INTEGER,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    started_at    REAL,
    ended_at      REAL,
    error         TEXT,
    result_json   TEXT,
    agent_count   INTEGER NOT NULL DEFAULT 0,
    phase_count   INTEGER NOT NULL DEFAULT 0,
    meta_json     TEXT
);

CREATE TABLE IF NOT EXISTS flow_phases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES flow_runs(id),
    seq         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    started_at  REAL NOT NULL,
    UNIQUE(run_id, seq)
);

CREATE TABLE IF NOT EXISTS flow_agents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES flow_runs(id),
    call_index   INTEGER NOT NULL,          -- monotonic per run; the resume key
    label        TEXT,
    phase        TEXT,
    prompt_sha   TEXT NOT NULL,             -- guards the resume cache
    status       TEXT NOT NULL DEFAULT 'running',  -- running|completed|failed|skipped
    started_at   REAL NOT NULL,
    ended_at     REAL,
    model        TEXT,
    api_calls    INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    result_json  TEXT,                       -- cached return value (text or structured)
    error        TEXT,
    UNIQUE(run_id, call_index)
);

CREATE TABLE IF NOT EXISTS flow_logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL REFERENCES flow_runs(id),
    ts        REAL NOT NULL,
    message   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_flow_agents_run ON flow_agents(run_id);
CREATE INDEX IF NOT EXISTS idx_flow_phases_run ON flow_phases(run_id);
CREATE INDEX IF NOT EXISTS idx_flow_logs_run ON flow_logs(run_id);
"""

_local = threading.local()


def db_path() -> Path:
    """Resolve the oc_flow DB path (``HERMES_OC_FLOW_DB`` override wins)."""
    override = os.environ.get("HERMES_OC_FLOW_DB", "").strip()
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root()
    except Exception:
        root = Path(os.path.expanduser("~/.hermes"))
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return root / "oc_flow.db"


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-local sqlite connection with the schema applied.

    WAL mode + a busy timeout lets a foreground reader (``hermes flow show``)
    and a background worker write the same DB without ``database is locked``.
    """
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


def new_run_id() -> str:
    return "flow_" + uuid.uuid4().hex[:12]


# --------------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------------- #

def create_run(
    *,
    run_id: str,
    name: str,
    description: str = "",
    script_path: str = "",
    script_sha: str = "",
    args: Any = None,
    background: bool = False,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO flow_runs
               (id, name, description, script_path, script_sha, args_json,
                status, background, created_at, updated_at, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, name, description, script_path, script_sha,
                json.dumps(args) if args is not None else None,
                "pending", 1 if background else 0, now, now,
                json.dumps(meta) if meta else None,
            ),
        )
        conn.commit()
    return run_id


def mark_run_started(run_id: str, pid: Optional[int] = None) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE flow_runs SET status='running', started_at=COALESCE(started_at,?), updated_at=?, pid=? WHERE id=?",
            (now, now, pid if pid is not None else os.getpid(), run_id),
        )
        conn.commit()


def finish_run(run_id: str, status: str, result: Any = None, error: Optional[str] = None) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE flow_runs SET status=?, ended_at=?, updated_at=?, result_json=?, error=? WHERE id=?",
            (
                status, now, now,
                json.dumps(result, default=str) if result is not None else None,
                error, run_id,
            ),
        )
        conn.commit()


def touch_run(run_id: str, *, agent_count: Optional[int] = None, phase_count: Optional[int] = None) -> None:
    # Static statements only — never compose SQL from a column list, even a
    # trusted one (keeps the query injection-proof and the linter quiet).
    now = _now()
    with connect() as conn:
        conn.execute("UPDATE flow_runs SET updated_at=? WHERE id=?", (now, run_id))
        if agent_count is not None:
            conn.execute("UPDATE flow_runs SET agent_count=? WHERE id=?", (agent_count, run_id))
        if phase_count is not None:
            conn.execute("UPDATE flow_runs SET phase_count=? WHERE id=?", (phase_count, run_id))
        conn.commit()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM flow_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Phases
# --------------------------------------------------------------------------- #

def add_phase(run_id: str, seq: int, title: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO flow_phases (run_id, seq, title, started_at) VALUES (?,?,?,?)",
            (run_id, seq, title, _now()),
        )
        conn.commit()


def list_phases(run_id: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_phases WHERE run_id=? ORDER BY seq", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Agents (the resume cache lives here)
# --------------------------------------------------------------------------- #

def get_cached_agent(run_id: str, call_index: int, prompt_sha: str) -> Optional[Dict[str, Any]]:
    """Return a completed agent record iff its prompt hash matches (resume)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM flow_agents WHERE run_id=? AND call_index=?",
            (run_id, call_index),
        ).fetchone()
    if not row:
        return None
    rec = dict(row)
    if rec.get("status") == "completed" and rec.get("prompt_sha") == prompt_sha:
        return rec
    return None


def start_agent(
    run_id: str, call_index: int, *, label: str = "", phase: str = "",
    prompt_sha: str = "", model: str = "",
) -> int:
    """Insert (or reset) an agent row in 'running' state; return its row id."""
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO flow_agents (run_id, call_index, label, phase, prompt_sha, status, started_at, model)
               VALUES (?,?,?,?,?,'running',?,?)
               ON CONFLICT(run_id, call_index) DO UPDATE SET
                 label=excluded.label, phase=excluded.phase, prompt_sha=excluded.prompt_sha,
                 status='running', started_at=excluded.started_at, ended_at=NULL,
                 result_json=NULL, error=NULL, model=excluded.model""",
            (run_id, call_index, label, phase, prompt_sha, now, model),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM flow_agents WHERE run_id=? AND call_index=?", (run_id, call_index)
        ).fetchone()
        return int(row["id"])


def finish_agent(
    run_id: str, call_index: int, *, status: str, result: Any = None,
    error: Optional[str] = None, api_calls: int = 0, output_tokens: int = 0,
    model: Optional[str] = None,
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE flow_agents SET status=?, ended_at=?, result_json=?, error=?,
                      api_calls=?, output_tokens=? WHERE run_id=? AND call_index=?""",
            (
                status, now,
                json.dumps(result, default=str) if result is not None else None,
                error, api_calls, output_tokens, run_id, call_index,
            ),
        )
        # Record the *effective* model the runner actually used (the start row
        # only knows the requested model, which is empty when inheriting config).
        if model:
            conn.execute(
                "UPDATE flow_agents SET model=? WHERE run_id=? AND call_index=?",
                (model, run_id, call_index),
            )
        conn.commit()


def list_agents(run_id: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_agents WHERE run_id=? ORDER BY call_index", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_log(run_id: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO flow_logs (run_id, ts, message) VALUES (?,?,?)",
            (run_id, _now(), message),
        )
        conn.commit()


def list_logs(run_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM flow_logs WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def decode_result(rec: Dict[str, Any]) -> Any:
    """Decode a cached agent/run result_json back into a Python value."""
    raw = rec.get("result_json")
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw
