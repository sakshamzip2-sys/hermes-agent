"""SQLite-backed store for agent teams: members, a shared task list, and a mailbox.

Ports Claude Code's *agent teams* substrate into v2: a lead agent and teammates
share a task list (with dependencies and atomic claiming) and message each other
through a mailbox. State lives in a standalone DB (``<root>/oc_teams.db``) so the
lead, the teammates (separate processes), and the human can all coordinate.

Task claiming is a single atomic conditional UPDATE (compare-and-swap): only the
member whose UPDATE affects a row wins it, so two teammates can never grab the
same task. Dependency gating runs inside a ``BEGIN IMMEDIATE`` transaction so the
check-then-claim is serialized per board. ``HERMES_OC_TEAMS_DB`` overrides the path.
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

TASK_PENDING = "pending"
TASK_IN_PROGRESS = "in_progress"
TASK_COMPLETED = "completed"

MEMBER_LEAD = "lead"
MEMBER_TEAMMATE = "teammate"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    goal        TEXT,
    status      TEXT NOT NULL DEFAULT 'active',   -- active|cleaned
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id        TEXT NOT NULL REFERENCES teams(id),
    name           TEXT NOT NULL,
    role           TEXT,
    kind           TEXT NOT NULL DEFAULT 'teammate',  -- lead|teammate
    bg_session_id  TEXT,
    status         TEXT NOT NULL DEFAULT 'active',     -- active|idle|shutdown
    created_at     REAL NOT NULL,
    UNIQUE(team_id, name)
);

CREATE TABLE IF NOT EXISTS team_tasks (
    id          TEXT PRIMARY KEY,
    team_id     TEXT NOT NULL REFERENCES teams(id),
    subject     TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    owner       TEXT NOT NULL DEFAULT '',      -- member name, '' = unclaimed
    depends_on  TEXT,                           -- JSON list of task ids
    created_by  TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS team_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id     TEXT NOT NULL REFERENCES teams(id),
    from_member TEXT NOT NULL,
    to_member   TEXT NOT NULL,                  -- member name or '*' (broadcast)
    body        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    read_flag   INTEGER NOT NULL DEFAULT 0      -- legacy; per-member read state lives in team_message_reads
);

-- Per-recipient read state. A broadcast (to_member='*') is one row but must be
-- independently readable by each member, so "read" is tracked per (message, member)
-- rather than with a single flag on the message.
CREATE TABLE IF NOT EXISTS team_message_reads (
    message_id  INTEGER NOT NULL REFERENCES team_messages(id),
    member      TEXT NOT NULL,
    read_at     REAL NOT NULL,
    PRIMARY KEY (message_id, member)
);

CREATE INDEX IF NOT EXISTS idx_team_tasks_team ON team_tasks(team_id);
CREATE INDEX IF NOT EXISTS idx_team_messages_team ON team_messages(team_id);
CREATE INDEX IF NOT EXISTS idx_team_members_team ON team_members(team_id);
"""

_local = threading.local()


def db_path() -> Path:
    override = os.environ.get("HERMES_OC_TEAMS_DB", "").strip()
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
    return root / "oc_teams.db"


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


def new_team_id() -> str:
    return "team_" + uuid.uuid4().hex[:10]


def new_task_id() -> str:
    return "task_" + uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- #
# Teams & members
# --------------------------------------------------------------------------- #

def create_team(team_id: str, name: str, goal: str = "", lead_name: str = "lead") -> str:
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO teams (id, name, goal, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (team_id, name, goal, "active", now, now),
        )
        conn.execute(
            "INSERT INTO team_members (team_id, name, role, kind, status, created_at) VALUES (?,?,?,?,?,?)",
            (team_id, lead_name, "lead", MEMBER_LEAD, "active", now),
        )
        conn.commit()
    return team_id


def get_team(team_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        return dict(row) if row else None


def list_teams(include_cleaned: bool = True) -> List[Dict[str, Any]]:
    with connect() as conn:
        if include_cleaned:
            rows = conn.execute("SELECT * FROM teams ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute("SELECT * FROM teams WHERE status='active' ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def set_team_status(team_id: str, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE teams SET status=?, updated_at=? WHERE id=?", (status, _now(), team_id))
        conn.commit()


def add_member(team_id: str, name: str, role: str = "", kind: str = MEMBER_TEAMMATE, bg_session_id: str = "") -> bool:
    """Add a member. Returns False if the name is already taken on this team."""
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO team_members (team_id, name, role, kind, bg_session_id, status, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (team_id, name, role, kind, bg_session_id, "active", _now()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def set_member_status(team_id: str, name: str, status: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE team_members SET status=? WHERE team_id=? AND name=?",
            (status, team_id, name),
        )
        conn.commit()


def set_member_session(team_id: str, name: str, bg_session_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE team_members SET bg_session_id=? WHERE team_id=? AND name=?",
            (bg_session_id, team_id, name),
        )
        conn.commit()


def get_member(team_id: str, name: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM team_members WHERE team_id=? AND name=?", (team_id, name)
        ).fetchone()
        return dict(row) if row else None


def list_members(team_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM team_members WHERE team_id=? ORDER BY id"
    params: tuple = (team_id,)
    if limit is not None:
        sql += " LIMIT ?"
        params = (team_id, limit)
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def active_teammates(team_id: str) -> List[Dict[str, Any]]:
    return [m for m in list_members(team_id) if m["kind"] == MEMBER_TEAMMATE and m["status"] == "active"]


# --------------------------------------------------------------------------- #
# Tasks (shared list with deps + atomic claim)
# --------------------------------------------------------------------------- #

def create_task(
    team_id: str, subject: str, *, description: str = "", depends_on: Optional[List[str]] = None,
    created_by: str = "", task_id: Optional[str] = None,
) -> str:
    tid = task_id or new_task_id()
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO team_tasks (id, team_id, subject, description, status, owner, depends_on, created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, team_id, subject, description, TASK_PENDING, "",
             json.dumps(depends_on) if depends_on else None, created_by, now, now),
        )
        conn.commit()
    return tid


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM team_tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def list_tasks(
    team_id: str, status: Optional[str] = None, limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    with connect() as conn:
        if status:
            sql = "SELECT * FROM team_tasks WHERE team_id=? AND status=? ORDER BY created_at"
            params: tuple = (team_id, status)
        else:
            sql = "SELECT * FROM team_tasks WHERE team_id=? ORDER BY created_at"
            params = (team_id,)
        if limit is not None:
            sql += " LIMIT ?"
            params = params + (limit,)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def _deps_met(conn: sqlite3.Connection, depends_on_json: Optional[str]) -> bool:
    if not depends_on_json:
        return True
    try:
        deps = json.loads(depends_on_json)
    except Exception:
        return True
    if not deps:
        return True
    # Per-dependency parameterized lookups (no dynamic SQL): every named
    # dependency must exist and be completed.
    for dep in set(deps):
        row = conn.execute("SELECT status FROM team_tasks WHERE id=?", (dep,)).fetchone()
        if row is None or row["status"] != TASK_COMPLETED:
            return False
    return True


def claimable_tasks(team_id: str) -> List[Dict[str, Any]]:
    """Pending, unowned tasks whose dependencies are all completed."""
    out = []
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM team_tasks WHERE team_id=? AND status=? AND owner='' ORDER BY created_at",
            (team_id, TASK_PENDING),
        ).fetchall()
        for r in rows:
            if _deps_met(conn, r["depends_on"]):
                out.append(dict(r))
    return out


def claim_task(task_id: str, member: str) -> bool:
    """Atomically claim a task for ``member``. Returns True iff this caller won.

    Uses BEGIN IMMEDIATE so the dependency check and the compare-and-swap UPDATE
    are serialized; the UPDATE's WHERE clause is the actual CAS guard.
    """
    with connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM team_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or row["status"] != TASK_PENDING or row["owner"]:
                conn.execute("ROLLBACK")
                return False
            if not _deps_met(conn, row["depends_on"]):
                conn.execute("ROLLBACK")
                return False
            cur = conn.execute(
                "UPDATE team_tasks SET owner=?, status=?, updated_at=? "
                "WHERE id=? AND owner='' AND status=?",
                (member, TASK_IN_PROGRESS, _now(), task_id, TASK_PENDING),
            )
            conn.execute("COMMIT")
            return cur.rowcount == 1
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            return False


def complete_task(task_id: str, member: str = "") -> bool:
    """Mark a task completed.

    When ``member`` is given (the teammate path), the caller may only complete a
    task it owns or one that is still unclaimed — never another member's task.
    With an empty ``member`` (the CLI / lead-override path) any non-completed
    task can be completed.
    """
    now = _now()
    with connect() as conn:
        if member:
            cur = conn.execute(
                "UPDATE team_tasks SET status=?, updated_at=? "
                "WHERE id=? AND status!=? AND (owner='' OR owner=?)",
                (TASK_COMPLETED, now, task_id, TASK_COMPLETED, member),
            )
        else:
            cur = conn.execute(
                "UPDATE team_tasks SET status=?, updated_at=? WHERE id=? AND status!=?",
                (TASK_COMPLETED, now, task_id, TASK_COMPLETED),
            )
        conn.commit()
        return cur.rowcount == 1


def reassign_task(task_id: str, member: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE team_tasks SET owner=?, status=?, updated_at=? WHERE id=?",
            (member, TASK_IN_PROGRESS, _now(), task_id),
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# Mailbox
# --------------------------------------------------------------------------- #

def send_message(team_id: str, from_member: str, to_member: str, body: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO team_messages (team_id, from_member, to_member, body, created_at) VALUES (?,?,?,?,?)",
            (team_id, from_member, to_member, body, _now()),
        )
        conn.commit()
        return int(cur.lastrowid or 0)


_INBOX_UNREAD_SQL = (
    "SELECT m.* FROM team_messages m WHERE m.team_id=? "
    "AND (m.to_member=? OR m.to_member='*') AND m.from_member!=? "
    "AND NOT EXISTS (SELECT 1 FROM team_message_reads r WHERE r.message_id=m.id AND r.member=?) "
    "ORDER BY m.id"
)
_INBOX_ALL_SQL = (
    "SELECT m.* FROM team_messages m WHERE m.team_id=? "
    "AND (m.to_member=? OR m.to_member='*') AND m.from_member!=? ORDER BY m.id"
)


def read_inbox(team_id: str, member: str, *, mark_read: bool = True, unread_only: bool = True) -> List[Dict[str, Any]]:
    """Return messages addressed to ``member`` (direct or broadcast); optionally mark read.

    Read state is tracked per (message, member) so a broadcast can be read once
    by each member independently — alice reading a broadcast does not hide it
    from bob.
    """
    with connect() as conn:
        if unread_only:
            rows = conn.execute(_INBOX_UNREAD_SQL, (team_id, member, member, member)).fetchall()
        else:
            rows = conn.execute(_INBOX_ALL_SQL, (team_id, member, member)).fetchall()
        msgs = [dict(r) for r in rows]
        if mark_read and msgs:
            now = _now()
            for m in msgs:
                conn.execute(
                    "INSERT OR IGNORE INTO team_message_reads (message_id, member, read_at) VALUES (?,?,?)",
                    (m["id"], member, now),
                )
            conn.commit()
        return msgs


def list_messages(team_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM team_messages WHERE team_id=? ORDER BY id DESC LIMIT ?", (team_id, limit)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def team_status_summary(team_id: str) -> Dict[str, Any]:
    members = list_members(team_id)
    tasks = list_tasks(team_id)
    by_status: Dict[str, int] = {}
    for t in tasks:
        by_status[t["status"]] = by_status.get(t["status"], 0) + 1
    return {
        "team": get_team(team_id),
        "members": members,
        "task_counts": by_status,
        "tasks_total": len(tasks),
    }
