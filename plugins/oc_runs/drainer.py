"""The single spine writer: drains each engine's outbox into ``oc_runs.db``.

The drainer is the ONLY writer to the spine, so seq assignment has one ordering
and there is no multi-writer contention. It is crash-safe: it appends to the
spine first, then marks the outbox row drained. If it dies in between, the row
stays undrained and the next pass re-appends. Re-appends are idempotent because
every drained event carries a stable dedupe identity: an explicit dedupe_key if
the producer set one, otherwise a synthesized ``outbox:<source>:<outbox_id>``
key. So even keyless progress/heartbeat events land in the spine exactly once.
"""

from __future__ import annotations

from typing import Any, Callable, ContextManager, Dict

from . import db as spine_db
from . import outbox
from .events import SCHEMA_VERSION

# A connect factory is anything that yields a DB connection as a context manager
# (e.g. plugins.oc_agents.db.connect).
ConnectFactory = Callable[[], ContextManager[Any]]


def _row_to_event(row: Dict[str, Any]) -> Dict[str, Any]:
    ev = {
        "schema_version": int(row.get("schema_version", SCHEMA_VERSION)),
        "ts": row["ts"],
        "run_id": row["run_id"],
        "parent_run_id": row.get("parent_run_id"),
        "source": row["source"],
        "type": row["type"],
        "agent_id": row.get("agent_id"),
        "team_id": row.get("team_id"),
        "payload": row.get("payload") or {},
        "dedupe_key": row.get("dedupe_key"),
    }
    if not ev["dedupe_key"]:
        # Stable per-outbox-row identity so a crash-replay dedupes even when the
        # producer set no key (progress, heartbeat).
        ev["dedupe_key"] = f"outbox:{ev['source']}:{row['outbox_id']}"
    return ev


def drain_append_only(connect: ConnectFactory, limit: int = 500) -> int:
    """Append undrained rows to the spine but do NOT mark them drained. Used to
    reason about / test the crash window between append and mark. Returns the
    number appended (each idempotent on the spine)."""
    with connect() as conn:
        undrained = outbox.fetch_undrained(conn, limit)
    for row in undrained:
        spine_db.append_event(_row_to_event(row))
    return len(undrained)


def drain(connect: ConnectFactory, limit: int = 500) -> int:
    """Move undrained outbox rows from an engine DB into the spine and mark them
    drained. Crash-safe and idempotent. Returns the number drained this pass."""
    with connect() as conn:
        undrained = outbox.fetch_undrained(conn, limit)
        drained_ids = []
        for row in undrained:
            spine_db.append_event(_row_to_event(row))
            drained_ids.append(row["outbox_id"])
        if drained_ids:
            outbox.mark_drained(conn, drained_ids)
            conn.commit()
        return len(drained_ids)
