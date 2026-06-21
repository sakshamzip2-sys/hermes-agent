"""SQLite-backed registry for background agent sessions (Agent View).

A *background session* is one headless agent run dispatched to run detached, so
you can fire several off, watch their state from one place, and step in only
when one needs you — the Claude-Code "agent view" concept, in the v2 idiom.

State lives in a standalone DB (``<root>/oc_agents.db``) so any ``hermes``
invocation can list/inspect sessions without a long-lived supervisor. Liveness
is reconciled on read: a row marked ``working`` whose process is gone is
demoted to ``failed`` (``reconcile_liveness``), which gives supervisor-like
behaviour without a separate daemon. ``HERMES_OC_AGENTS_DB`` overrides the path
(used by the detached worker handoff and by tests).
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

# The run-event spine (Feature B). Emission is best-effort and defensive: if the
# oc_runs plugin is ever absent, oc_agents keeps working with no spine events
# (graceful degrade, matches the migration-ordering open item). Events are
# enqueued into a per-DB outbox in the SAME transaction as the state mutation,
# then a single drainer moves them to the spine.
try:
    from plugins.oc_runs import events as _run_events
    from plugins.oc_runs import outbox as _run_outbox

    _SPINE_ENABLED = True
except Exception:  # pragma: no cover - exercised only when oc_runs is removed
    _SPINE_ENABLED = False


def _emit(
    conn,
    event_type: str,
    session_id: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    parent_id: str = "",
    agent_id: str = "",
) -> None:
    """Enqueue one spine event into the outbox on this connection (same txn as
    the caller's mutation). Never raises: a spine hiccup must not break a run."""
    if not _SPINE_ENABLED:
        return
    try:
        _run_outbox.enqueue(
            conn,
            _run_events.build_event(
                f"agents:{session_id}",
                event_type,
                source=_run_events.SOURCE_AGENTS,
                parent_run_id=(f"agents:{parent_id}" if parent_id else None),
                agent_id=agent_id or None,
                payload=payload,
                dedupe_key=dedupe_key,
            ),
        )
    except Exception:  # pragma: no cover - defensive
        pass


# Session lifecycle states (mirrors agent-view semantics).
STATE_PENDING = "pending"
STATE_WORKING = "working"
STATE_NEEDS_INPUT = "needs_input"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_STOPPED = "stopped"

LIVE_STATES = (STATE_PENDING, STATE_WORKING, STATE_NEEDS_INPUT)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bg_sessions (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    cwd          TEXT,
    model        TEXT,
    provider     TEXT,
    toolsets     TEXT,                 -- JSON list or NULL (inherit)
    pid          INTEGER,
    agent_session_id TEXT,             -- the hermes_state session id, if any
    parent_id    TEXT,                 -- spawning session/teammate, if any
    kind         TEXT NOT NULL DEFAULT 'agent',   -- agent|teammate|...
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    started_at   REAL,
    ended_at     REAL,
    last_summary TEXT,                 -- latest activity line (cheap row summary)
    api_calls    INTEGER NOT NULL DEFAULT 0,
    result       TEXT,
    error        TEXT,
    log_path     TEXT,
    pinned       INTEGER NOT NULL DEFAULT 0,
    meta         TEXT
);

CREATE INDEX IF NOT EXISTS idx_bg_sessions_status ON bg_sessions(status);
CREATE INDEX IF NOT EXISTS idx_bg_sessions_created ON bg_sessions(created_at);

-- Granular per-session activity (tool start/complete, thinking lines). The
-- worker appends here on every progress callback so the cockpit can show a
-- live play-by-play, not just the latest one-line summary.
CREATE TABLE IF NOT EXISTS bg_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'activity',   -- activity|tool|thinking|...
    text        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bg_events_session ON bg_events(session_id, id);

-- User-sent messages for a running agent ("steer it live"). The worker drains
-- these when the agent asks for input (clarify) and as follow-up turns after
-- the current run finishes, so you can answer questions or queue more work.
CREATE TABLE IF NOT EXISTS bg_inbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    ts          REAL NOT NULL,
    message     TEXT NOT NULL,
    consumed    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bg_inbox_session ON bg_inbox(session_id, consumed, id);
"""

_local = threading.local()


def db_path() -> Path:
    override = os.environ.get("HERMES_OC_AGENTS_DB", "").strip()
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
    return root / "oc_agents.db"


def logs_dir() -> Path:
    d = db_path().parent / "oc_agents_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
        if _SPINE_ENABLED:
            try:
                _run_outbox.ensure_outbox(conn)
            except Exception:  # pragma: no cover - defensive
                pass
        conn.commit()
        _local.conn = conn
        _local.path = path
    yield conn


def _now() -> float:
    return time.time()


def new_session_id() -> str:
    return uuid.uuid4().hex[:8]


def _slug_from_prompt(prompt: str) -> str:
    words = (prompt or "").strip().split()
    return "-".join(words[:5])[:48] or "session"


# --------------------------------------------------------------------------- #

def create_session(
    *,
    session_id: str,
    prompt: str,
    name: str = "",
    cwd: str = "",
    model: str = "",
    provider: str = "",
    toolsets: Optional[List[str]] = None,
    parent_id: str = "",
    kind: str = "agent",
    log_path: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO bg_sessions
               (id, name, prompt, status, cwd, model, provider, toolsets,
                parent_id, kind, created_at, updated_at, log_path, meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                session_id, name or _slug_from_prompt(prompt), prompt, STATE_PENDING,
                cwd, model, provider,
                json.dumps(toolsets) if toolsets else None,
                parent_id, kind, now, now, log_path,
                json.dumps(meta) if meta else None,
            ),
        )
        _emit(
            conn, "run.created", session_id,
            payload={"name": name or _slug_from_prompt(prompt), "kind": kind,
                     "cwd": cwd, "prompt": (prompt or "")[:200]},
            dedupe_key="created", parent_id=parent_id,
            agent_id=name or _slug_from_prompt(prompt),
        )
        conn.commit()
    return session_id


def set_pid(session_id: str, pid: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE bg_sessions SET pid=?, updated_at=? WHERE id=?",
            (pid, _now(), session_id),
        )
        conn.commit()


def mark_working(session_id: str, agent_session_id: str = "") -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE bg_sessions SET status=?, started_at=COALESCE(started_at,?),
               updated_at=?, agent_session_id=COALESCE(NULLIF(?,''), agent_session_id)
               WHERE id=?""",
            (STATE_WORKING, now, now, agent_session_id, session_id),
        )
        _emit(conn, "run.status", session_id, payload={"status": "running"},
              dedupe_key="status:working")
        conn.commit()


def update_summary(session_id: str, summary: str, *, api_calls: Optional[int] = None) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE bg_sessions SET last_summary=?, updated_at=? WHERE id=?",
            (summary[:500], now, session_id),
        )
        if api_calls is not None:
            conn.execute("UPDATE bg_sessions SET api_calls=? WHERE id=?", (api_calls, session_id))
        conn.commit()


# Max events retained per session. The detail API reads at most 300; keeping a
# generous tail bounds storage without losing useful recent history.
EVENTS_PER_SESSION_CAP = 1000


def add_event(session_id: str, text: str, kind: str = "activity") -> None:
    """Append one granular activity event for a session. Best-effort: the worker
    calls this from its progress callback, so it must be cheap and tolerant.

    Storage is bounded with a per-session FIFO cap. The prune is run only every
    ~100th insert (keyed off the global rowid) so the common path stays a single
    cheap INSERT, while the table can't grow without limit over a long-lived
    deployment that never deletes finished sessions."""
    if not text:
        return
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO bg_events (session_id, ts, kind, text) VALUES (?,?,?,?)",
            (session_id, _now(), kind, text[:500]),
        )
        if cur.lastrowid and cur.lastrowid % 100 == 0:
            conn.execute(
                """DELETE FROM bg_events
                   WHERE session_id=? AND id NOT IN (
                       SELECT id FROM bg_events WHERE session_id=?
                       ORDER BY id DESC LIMIT ?
                   )""",
                (session_id, session_id, EVENTS_PER_SESSION_CAP),
            )
        _emit(conn, "run.progress", session_id, payload={"kind": kind, "text": text[:500]})
        conn.commit()


def list_events(
    session_id: str, after_id: int = 0, limit: int = 200
) -> List[Dict[str, Any]]:
    """Return up to ``limit`` events for a session with id > ``after_id`` (for
    incremental polling), oldest-first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bg_events WHERE session_id=? AND id>? ORDER BY id LIMIT ?",
            (session_id, after_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def add_inbox_message(session_id: str, message: str) -> int:
    """Queue a user message for a running agent. Returns the new message id."""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO bg_inbox (session_id, ts, message, consumed) VALUES (?,?,?,0)",
            (session_id, _now(), message[:4000]),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


def pop_inbox_message(session_id: str) -> Optional[str]:
    """Atomically take the oldest unconsumed inbox message (marking it consumed)
    and return its text, or None if the inbox is empty."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, message FROM bg_inbox WHERE session_id=? AND consumed=0 ORDER BY id LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE bg_inbox SET consumed=1 WHERE id=?", (row["id"],))
        conn.commit()
        return row["message"]


def pending_inbox(session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Unconsumed queued messages for a session (for the UI), oldest-first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bg_inbox WHERE session_id=? AND consumed=0 ORDER BY id LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def set_needs_input(session_id: str, question: str) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE bg_sessions SET status=?, last_summary=?, updated_at=? WHERE id=?",
            (STATE_NEEDS_INPUT, f"needs input: {question}"[:500], now, session_id),
        )
        conn.commit()


def finish_session(
    session_id: str, status: str, *, result: str = "", error: str = "", api_calls: Optional[int] = None,
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """UPDATE bg_sessions SET status=?, ended_at=?, updated_at=?, result=?, error=?
               WHERE id=?""",
            (status, now, now, result or None, error or None, session_id),
        )
        if api_calls is not None:
            conn.execute("UPDATE bg_sessions SET api_calls=? WHERE id=?", (api_calls, session_id))
        _term = "run.failed" if status == STATE_FAILED else "run.completed"
        _emit(conn, _term, session_id,
              payload={"status": status, "result": (result or "")[:500],
                       "error": (error or "")[:500]},
              dedupe_key="terminal")
        conn.commit()


def set_pinned(session_id: str, pinned: bool) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE bg_sessions SET pinned=?, updated_at=? WHERE id=?",
            (1 if pinned else 0, _now(), session_id),
        )
        conn.commit()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM bg_sessions WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None


def list_sessions(limit: int = 100, include_done: bool = True) -> List[Dict[str, Any]]:
    with connect() as conn:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM bg_sessions ORDER BY pinned DESC, created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM bg_sessions WHERE status IN (?,?,?) ORDER BY pinned DESC, created_at DESC LIMIT ?",
                (*LIVE_STATES, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def delete_session(session_id: str) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM bg_sessions WHERE id=?", (session_id,))
        conn.execute("DELETE FROM bg_events WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM bg_inbox WHERE session_id=?", (session_id,))
        conn.commit()
        return cur.rowcount > 0


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    except Exception:
        return False


def reconcile_liveness() -> int:
    """Demote sessions that claim to be live but whose process is gone.

    This is the 'supervisor' behaviour without a daemon: any read of the
    session list first reconciles stale rows. Returns the number demoted.
    """
    demoted = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, pid, status, started_at FROM bg_sessions WHERE status IN (?,?,?)",
            LIVE_STATES,
        ).fetchall()
        for r in rows:
            # A row that has a pid which is dead is failed. A row still
            # 'pending' with no pid yet AND older than a grace window likely
            # failed to launch.
            pid = r["pid"]
            if pid and not _pid_alive(pid):
                conn.execute(
                    "UPDATE bg_sessions SET status=?, error=COALESCE(error,'process exited without finalizing'), "
                    "ended_at=COALESCE(ended_at,?), updated_at=? WHERE id=?",
                    (STATE_FAILED, _now(), _now(), r["id"]),
                )
                demoted += 1
            elif r["status"] == STATE_PENDING and not pid:
                # A pending row with no pid that's older than the grace window
                # never managed to launch its worker — fail it.
                created = conn.execute("SELECT created_at FROM bg_sessions WHERE id=?", (r["id"],)).fetchone()
                if created and (_now() - created["created_at"]) > 60:
                    conn.execute(
                        "UPDATE bg_sessions SET status=?, error='worker never started', "
                        "ended_at=?, updated_at=? WHERE id=?",
                        (STATE_FAILED, _now(), _now(), r["id"]),
                    )
                    demoted += 1
        if demoted:
            conn.commit()
    return demoted


def counts() -> Dict[str, int]:
    out: Dict[str, int] = {}
    with connect() as conn:
        for row in conn.execute("SELECT status, COUNT(*) c FROM bg_sessions GROUP BY status"):
            out[row["status"]] = row["c"]
    return out
