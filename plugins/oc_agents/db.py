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
