"""SQLite-backed durable state for the Runtime Memory Supervisor (RMS).

This is the resumable spine that makes the supervisor crash-safe.  Everything the
supervisor needs to survive a gateway restart lives here, never memory-only:

* ``write_queue`` — the FAIL-CLOSED durable write queue.  A write to a down
  store is journaled here and drained on recovery; it is NEVER silently
  dropped.  Idempotent by a stable ``dedup_key`` (``INSERT OR IGNORE``), so a
  re-enqueue or re-drain cannot double-apply.  Terminal failures land in a
  ``dead_letter`` state (still in the DB, never lost).
* ``jobs`` — supervised background memory jobs (extract/write, compaction,
  retention, recall-eval) with a lease + heartbeat so a stuck/dead job is
  re-enqueued under a retry budget and permanent failures dead-letter.  Idempotent
  by ``UNIQUE(job_type, period_key)``.
* ``store_health`` — the mirrored per-store circuit-breaker state so the
  aggregator/CLI can SEE that a store is down (the fix for the silent-degradation
  finding) and the breaker can be restored on cold start.
* ``supervisor_status`` — a single-row heartbeat (``last_tick_at``) that the
  watchdog reads, plus counters surfaced in health.
* ``supervisor_leader`` — the single-writer lease row (boot-id + pid + nonce
  holder token) so two gateway processes do not both drain the queue.

Pattern mirrors ``plugins/oc_flow/db.py``: a standalone DB at
``$HERMES_HOME/mem_supervisor.db`` (override ``HERMES_MEM_SUPERVISOR_DB``), WAL
mode + ``busy_timeout`` for concurrent readers, a thread-local connection, and
an additive ``CREATE TABLE IF NOT EXISTS`` schema so reopening an existing DB is
always safe (resumable, never destructive).

No em dashes in emitted text (house rule).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

# Write-queue row states.
WQ_PENDING = "pending"
WQ_INFLIGHT = "inflight"
WQ_DONE = "done"
WQ_DEAD = "dead_letter"

# Job row states.
JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
JOB_DEAD = "dead_letter"
_JOB_LIVE_STATES = (JOB_PENDING, JOB_RUNNING)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS write_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key       TEXT NOT NULL UNIQUE,      -- sha256(store|op|normalized payload)
    store           TEXT NOT NULL,
    op              TEXT NOT NULL DEFAULT 'write',
    payload_json    TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|inflight|done|dead_letter
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    next_attempt_at REAL NOT NULL DEFAULT 0,   -- wall-clock epoch; backoff gate
    lease_token     TEXT,                       -- claimer identity while inflight
    lease_until     REAL,                       -- wall-clock epoch; inflight lease
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type        TEXT NOT NULL,
    period_key      TEXT NOT NULL DEFAULT '',  -- idempotency window (e.g. a date)
    payload_json    TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|failed|dead_letter
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 5,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    lease_token     TEXT,                       -- owning worker identity
    lease_until     REAL,
    last_progress_ts REAL,                      -- wall-clock heartbeat (NOT bare pid)
    last_error      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(job_type, period_key)
);

CREATE TABLE IF NOT EXISTS store_health (
    store                 TEXT PRIMARY KEY,
    state                 TEXT NOT NULL DEFAULT 'closed',
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    last_probe_at         REAL,
    last_change_at        REAL,
    last_error            TEXT,
    updated_at            REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_status (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    last_tick_at  REAL,
    started_at    REAL,
    tick_count    INTEGER NOT NULL DEFAULT 0,
    boot_id       TEXT,
    pid           INTEGER,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_leader (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    holder_token  TEXT,
    lease_until   REAL,
    updated_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wq_store_status ON write_queue(store, status);
CREATE INDEX IF NOT EXISTS idx_wq_status ON write_queue(status);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""

_local = threading.local()


def db_path() -> Path:
    """Resolve the supervisor DB path.  ``HERMES_MEM_SUPERVISOR_DB`` wins (used
    by tests + the dispatcher handoff), else ``$HERMES_HOME/mem_supervisor.db``.

    This env var is a PATH override only (mirrors ``HERMES_OC_FLOW_DB``); it is
    not behavioral config, which lives in ``config.yaml`` per the house rule.
    """
    override = os.environ.get("HERMES_MEM_SUPERVISOR_DB", "").strip()
    if override:
        p = Path(override).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    try:
        from hermes_constants import get_hermes_home

        root = Path(get_hermes_home())
    except Exception:
        root = Path(os.path.expanduser("~/.hermes"))
    root.mkdir(parents=True, exist_ok=True)
    return root / "mem_supervisor.db"


def lock_path() -> Path:
    """Path of the advisory-lock sidecar file used by the single-writer lease."""
    return Path(str(db_path()) + ".lock")


@contextmanager
def connect() -> Generator[sqlite3.Connection, None, None]:
    """Yield a thread-local sqlite connection with the schema applied (WAL +
    busy_timeout), reopening if the resolved path changed (tests swap the env)."""
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


def close_local() -> None:
    """Close + drop the thread-local connection (used by tests between cases)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    for attr in ("conn", "path"):
        if hasattr(_local, attr):
            try:
                delattr(_local, attr)
            except Exception:
                pass


def _now() -> float:
    return time.time()


def make_dedup_key(store: str, op: str, payload: Any) -> str:
    """Stable idempotency key: sha256 over store + op + a canonical JSON of the
    payload with volatile keys (timestamps / ids) excluded.

    Excluding volatile metadata means re-enqueuing 'the same logical write' maps
    to the same row, so ``INSERT OR IGNORE`` makes re-enqueue a true no-op and a
    re-drain after a crash cannot double-apply.
    """
    if isinstance(payload, dict):
        volatile = {"ts", "timestamp", "created_at", "updated_at", "_at", "nonce", "request_id"}
        norm = {k: payload[k] for k in sorted(payload) if k not in volatile}
        body = json.dumps(norm, sort_keys=True, default=str)
    else:
        body = json.dumps(payload, sort_keys=True, default=str)
    raw = f"{store}\x1f{op}\x1f{body}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- #
# Write queue (fail-closed)
# --------------------------------------------------------------------------- #

def enqueue_write(
    store: str,
    payload: Any,
    *,
    op: str = "write",
    max_attempts: int = 5,
    dedup_key: Optional[str] = None,
) -> str:
    """Durably enqueue a write to *store*.  Idempotent by ``dedup_key``.

    Returns the dedup_key (the stable op-id).  A duplicate enqueue is a no-op
    that returns the same key, so a caller can safely re-enqueue after a crash.
    """
    key = dedup_key or make_dedup_key(store, op, payload)
    now = _now()
    with connect() as conn:
        # next_attempt_at=0 means 'drainable immediately' (no backoff on a fresh
        # write).  Backoff is applied only on a failed drain via fail_write, so a
        # new write is never gated behind a wall-clock timestamp.  Keeping this 0
        # also makes the gate independent of which clock the caller drives.
        conn.execute(
            """INSERT OR IGNORE INTO write_queue
               (dedup_key, store, op, payload_json, status, attempts, max_attempts,
                next_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,?,0,?,0,?,?)""",
            (key, store, op, json.dumps(payload, default=str), WQ_PENDING,
             max_attempts, now, now),
        )
        conn.commit()
    return key


def claim_next_write(store: str, *, lease_token: str, lease_s: float, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Atomically claim the next drainable pending row for *store* (oldest first,
    backoff gate respected) by leasing it to *lease_token*.  Returns the claimed
    row dict, or None if nothing is drainable.

    The claim flips ``pending -> inflight`` under the row's id in a single guarded
    UPDATE so two drainers (should the lease ever overlap) cannot both claim the
    same row.
    """
    ts = _now() if now is None else now
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM write_queue
               WHERE store=? AND status=? AND next_attempt_at<=?
               ORDER BY id LIMIT 1""",
            (store, WQ_PENDING, ts),
        ).fetchone()
        if not row:
            return None
        cur = conn.execute(
            """UPDATE write_queue
               SET status=?, lease_token=?, lease_until=?, updated_at=?
               WHERE id=? AND status=?""",
            (WQ_INFLIGHT, lease_token, ts + lease_s, ts, row["id"], WQ_PENDING),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None  # lost the race
        claimed = conn.execute("SELECT * FROM write_queue WHERE id=?", (row["id"],)).fetchone()
        return dict(claimed) if claimed else None


def ack_write(row_id: int) -> None:
    """Mark a claimed write done (drained successfully)."""
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE write_queue SET status=?, lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?",
            (WQ_DONE, now, row_id),
        )
        conn.commit()


def fail_write(
    row_id: int,
    *,
    error: str,
    backoff_at: Optional[float] = None,
    permanent: bool = False,
) -> str:
    """Record a failed drain attempt.  Increments ``attempts``; re-queues with a
    backoff gate, or dead-letters when the attempt budget is exhausted or the
    failure is permanent.  Returns the resulting status."""
    now = _now()
    with connect() as conn:
        row = conn.execute("SELECT attempts, max_attempts FROM write_queue WHERE id=?", (row_id,)).fetchone()
        if not row:
            return WQ_DEAD
        attempts = int(row["attempts"]) + 1
        if permanent or attempts >= int(row["max_attempts"]):
            conn.execute(
                "UPDATE write_queue SET status=?, attempts=?, last_error=?, "
                "lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                (WQ_DEAD, attempts, error, now, row_id),
            )
            conn.commit()
            return WQ_DEAD
        nxt = backoff_at if backoff_at is not None else now
        conn.execute(
            "UPDATE write_queue SET status=?, attempts=?, last_error=?, next_attempt_at=?, "
            "lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?",
            (WQ_PENDING, attempts, error, nxt, now, row_id),
        )
        conn.commit()
        return WQ_PENDING


def reclaim_stale_inflight(*, now: Optional[float] = None) -> int:
    """Return any ``inflight`` write whose lease expired back to ``pending`` so a
    crash mid-drain does not strand the row.  Idempotency (``dedup_key`` +
    client-supplied id at the store) makes the re-drain safe.  Returns the count
    reclaimed."""
    ts = _now() if now is None else now
    with connect() as conn:
        cur = conn.execute(
            "UPDATE write_queue SET status=?, lease_token=NULL, lease_until=NULL, updated_at=? "
            "WHERE status=? AND (lease_until IS NULL OR lease_until < ?)",
            (WQ_PENDING, ts, WQ_INFLIGHT, ts),
        )
        conn.commit()
        return cur.rowcount


def queue_depth(store: Optional[str] = None, *, status: str = WQ_PENDING) -> int:
    with connect() as conn:
        if store is None:
            row = conn.execute("SELECT COUNT(*) AS n FROM write_queue WHERE status=?", (status,)).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM write_queue WHERE store=? AND status=?", (store, status)
            ).fetchone()
        return int(row["n"]) if row else 0


def evict_oldest_over_cap(store: str, max_depth: int) -> int:
    """If pending depth for *store* exceeds *max_depth*, dead-letter the oldest
    overflow rows (never silently drop).  Returns the number evicted."""
    now = _now()
    with connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM write_queue WHERE store=? AND status=?", (store, WQ_PENDING)
        ).fetchone()["n"]
        overflow = int(n) - int(max_depth)
        if overflow <= 0:
            return 0
        ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM write_queue WHERE store=? AND status=? ORDER BY id LIMIT ?",
                (store, WQ_PENDING, overflow),
            ).fetchall()
        ]
        for rid in ids:
            conn.execute(
                "UPDATE write_queue SET status=?, last_error=?, updated_at=? WHERE id=?",
                (WQ_DEAD, "evicted: queue over max_depth", now, rid),
            )
        conn.commit()
        return len(ids)


def get_write(dedup_key: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM write_queue WHERE dedup_key=?", (dedup_key,)).fetchone()
        return dict(row) if row else None


def list_writes(*, status: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    with connect() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM write_queue ORDER BY id LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM write_queue WHERE status=? ORDER BY id LIMIT ?", (status, limit)
            ).fetchall()
        return [dict(r) for r in rows]


def dead_letter_count(store: Optional[str] = None) -> int:
    return queue_depth(store, status=WQ_DEAD)


# --------------------------------------------------------------------------- #
# Jobs (supervised background work)
# --------------------------------------------------------------------------- #

def enqueue_job(
    job_type: str,
    *,
    period_key: str = "",
    payload: Any = None,
    max_attempts: int = 5,
) -> bool:
    """Enqueue a job idempotently by ``(job_type, period_key)``.  Returns True if
    a new row was created, False if one already existed (idempotent schedule)."""
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO jobs
               (job_type, period_key, payload_json, status, attempts, max_attempts,
                next_attempt_at, created_at, updated_at)
               VALUES (?,?,?,?,0,?,?,?,?)""",
            (job_type, period_key, json.dumps(payload, default=str) if payload is not None else None,
             JOB_PENDING, max_attempts, now, now, now),
        )
        conn.commit()
        return cur.rowcount == 1


def claim_job(job_id: int, *, lease_token: str, lease_s: float) -> bool:
    """Claim a pending job for a worker identity.  Returns True if claimed."""
    now = _now()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status=?, lease_token=?, lease_until=?, last_progress_ts=?, updated_at=? "
            "WHERE id=? AND status=?",
            (JOB_RUNNING, lease_token, now + lease_s, now, now, job_id, JOB_PENDING),
        )
        conn.commit()
        return cur.rowcount == 1


def heartbeat_job(job_id: int, *, lease_token: str, lease_s: float) -> None:
    """Touch a running job's wall-clock progress + extend its lease."""
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET last_progress_ts=?, lease_until=?, updated_at=? WHERE id=? AND lease_token=?",
            (now, now + lease_s, now, job_id, lease_token),
        )
        conn.commit()


def finish_job(job_id: int, *, status: str = JOB_DONE, error: Optional[str] = None) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE jobs SET status=?, last_error=?, lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?",
            (status, error, now, job_id),
        )
        conn.commit()


def reconcile_jobs(*, stuck_after_s: float, now: Optional[float] = None) -> int:
    """Re-enqueue or dead-letter jobs whose owner is provably dead.

    A running job is DEAD if its lease expired OR its ``last_progress_ts`` is
    older than *stuck_after_s* (a wall-clock heartbeat cap, NOT a bare
    ``os.kill`` check, which PID reuse defeats).  Dead jobs with attempts left
    re-enqueue (the caller applies the backoff); past the budget they
    dead-letter.  Returns the number of rows reconciled.
    """
    ts = _now() if now is None else now
    reconciled = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, attempts, max_attempts, lease_until, last_progress_ts FROM jobs WHERE status=?",
            (JOB_RUNNING,),
        ).fetchall()
        for r in rows:
            lease_until = r["lease_until"]
            progress = r["last_progress_ts"]
            lease_dead = lease_until is None or lease_until < ts
            progress_dead = progress is None or (ts - progress) > stuck_after_s
            if not (lease_dead and progress_dead):
                continue
            attempts = int(r["attempts"]) + 1
            if attempts >= int(r["max_attempts"]):
                conn.execute(
                    "UPDATE jobs SET status=?, attempts=?, last_error=?, "
                    "lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?",
                    (JOB_DEAD, attempts, "exceeded retry budget after stall", ts, r["id"]),
                )
            else:
                conn.execute(
                    "UPDATE jobs SET status=?, attempts=?, last_error=?, "
                    "lease_token=NULL, lease_until=NULL, next_attempt_at=?, updated_at=? WHERE id=?",
                    (JOB_PENDING, attempts, "re-enqueued after stall", ts, ts, r["id"]),
                )
            reconciled += 1
        if reconciled:
            conn.commit()
    return reconciled


def list_jobs(*, status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    with connect() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Store health (the visible signal)
# --------------------------------------------------------------------------- #

def upsert_store_health(
    store: str,
    *,
    state: str,
    consecutive_failures: int = 0,
    last_probe_at: Optional[float] = None,
    last_change_at: Optional[float] = None,
    last_error: Optional[str] = None,
) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO store_health
               (store, state, consecutive_failures, last_probe_at, last_change_at, last_error, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(store) DO UPDATE SET
                 state=excluded.state,
                 consecutive_failures=excluded.consecutive_failures,
                 last_probe_at=COALESCE(excluded.last_probe_at, store_health.last_probe_at),
                 last_change_at=COALESCE(excluded.last_change_at, store_health.last_change_at),
                 last_error=excluded.last_error,
                 updated_at=excluded.updated_at""",
            (store, state, consecutive_failures, last_probe_at, last_change_at, last_error, now),
        )
        conn.commit()


def get_store_health(store: Optional[str] = None) -> List[Dict[str, Any]]:
    with connect() as conn:
        if store is None:
            rows = conn.execute("SELECT * FROM store_health ORDER BY store").fetchall()
        else:
            rows = conn.execute("SELECT * FROM store_health WHERE store=?", (store,)).fetchall()
        return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Supervisor status (watchdog heartbeat)
# --------------------------------------------------------------------------- #

def record_tick(*, boot_id: str, pid: int, now: Optional[float] = None) -> None:
    """Write the loop heartbeat FIRST each tick (the watchdog signal)."""
    ts = _now() if now is None else now
    with connect() as conn:
        conn.execute(
            """INSERT INTO supervisor_status (id, last_tick_at, started_at, tick_count, boot_id, pid, updated_at)
               VALUES (1, ?, ?, 1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 last_tick_at=excluded.last_tick_at,
                 started_at=COALESCE(supervisor_status.started_at, excluded.started_at),
                 tick_count=supervisor_status.tick_count + 1,
                 boot_id=excluded.boot_id, pid=excluded.pid, updated_at=excluded.updated_at""",
            (ts, ts, boot_id, pid, ts),
        )
        conn.commit()


def get_status() -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM supervisor_status WHERE id=1").fetchone()
        return dict(row) if row else None


def heartbeat_stale(*, max_age_s: float, now: Optional[float] = None) -> bool:
    """True iff the loop heartbeat is older than *max_age_s* (watchdog trigger).

    A never-started supervisor (no row) is reported as NOT stale, so the watchdog
    does not thrash before the first tick; the start hook is responsible for the
    initial arm.
    """
    ts = _now() if now is None else now
    st = get_status()
    if not st or st.get("last_tick_at") is None:
        return False
    return (ts - float(st["last_tick_at"])) > max_age_s


# --------------------------------------------------------------------------- #
# Leader lease (single-writer; the flock half lives in lease.py)
# --------------------------------------------------------------------------- #

def try_acquire_lease(*, holder_token: str, lease_s: float, now: Optional[float] = None) -> bool:
    """Acquire/renew the leader lease in the DB.  Returns True if *holder_token*
    now holds it.  A non-holder wins only after the prior lease expired."""
    ts = _now() if now is None else now
    with connect() as conn:
        row = conn.execute("SELECT holder_token, lease_until FROM supervisor_leader WHERE id=1").fetchone()
        if row is None:
            conn.execute(
                "INSERT OR IGNORE INTO supervisor_leader (id, holder_token, lease_until, updated_at) VALUES (1,?,?,?)",
                (holder_token, ts + lease_s, ts),
            )
            conn.commit()
            row = conn.execute("SELECT holder_token, lease_until FROM supervisor_leader WHERE id=1").fetchone()
            return bool(row and row["holder_token"] == holder_token)
        held_by = row["holder_token"]
        lease_until = row["lease_until"] or 0
        if held_by == holder_token or lease_until < ts:
            conn.execute(
                "UPDATE supervisor_leader SET holder_token=?, lease_until=?, updated_at=? WHERE id=1",
                (holder_token, ts + lease_s, ts),
            )
            conn.commit()
            return True
        return False


def get_leader() -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM supervisor_leader WHERE id=1").fetchone()
        return dict(row) if row else None


def release_lease(*, holder_token: str) -> None:
    now = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE supervisor_leader SET lease_until=0, updated_at=? WHERE id=1 AND holder_token=?",
            (now, holder_token),
        )
        conn.commit()
