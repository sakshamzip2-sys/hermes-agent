"""Resumable live-stream core for the parallel-agents cockpit SSE route.

This is the pure, testable engine the HTTP route composes; it contains no HTTP
and no asyncio. The cockpit opens an SSE stream that:

1. sends a connect-time ``snapshot`` (full current state) so a fresh client
   renders immediately, then
2. streams ``deltas`` (new spine events past the client's cursor) as they land.

The client's resume cursor is the durable ``seq`` carried in the SSE ``id:``
field (the ``Last-Event-ID`` header on reconnect). The durable spine
(``oc_runs.db``) is the real backing log: any cursor can always be replayed from
it. A bounded in-memory ``RingBuffer`` is a fast path for the common case where
a client reconnects after a brief gap and the events it missed are still hot in
memory; when the cursor predates the ring (or the ring is empty) we fall back to
the spine so no event is ever lost.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Deque, Dict, List

from plugins.oc_runs import db as spine_db
from plugins.parallel_view import projection

DEFAULT_CAPACITY = 1024


class RingBuffer:
    """A bounded, in-memory ring of recent spine events, ordered by ``seq``.

    Events are appended in seq order (the spine assigns monotonic seqs). When the
    ring is full the oldest event is evicted. ``events_since`` serves the hot-path
    resume; if a cursor predates the retained window the caller must fall back to
    the durable spine (see :func:`resume`).
    """

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._events: Deque[Dict[str, Any]] = deque(maxlen=capacity)

    def append(self, event: Dict[str, Any]) -> None:
        """Append one event (evicting the oldest when at capacity)."""
        self._events.append(event)

    def events_since(self, seq: int) -> List[Dict[str, Any]]:
        """Return retained events with ``seq`` strictly greater than ``seq``."""
        return [e for e in self._events if e["seq"] > seq]

    def oldest_seq(self) -> int:
        """Seq of the oldest retained event, or 0 when the ring is empty."""
        return self._events[0]["seq"] if self._events else 0

    def latest_seq(self) -> int:
        """Seq of the newest retained event, or 0 when the ring is empty."""
        return self._events[-1]["seq"] if self._events else 0

    def covers(self, cursor: int) -> bool:
        """True if a resume from ``cursor`` can be served wholly from the ring.

        The ring covers a cursor when it is non-empty and the cursor is at or
        after the oldest retained event minus one, i.e. every event the client
        missed (seq > cursor) is still in memory. Concretely: the cursor must be
        >= (oldest_seq - 1) so the first event after it is the oldest retained.
        """
        if not self._events:
            return False
        return cursor >= self.oldest_seq() - 1


def snapshot(since_seq: int = 0) -> Dict[str, Any]:
    """Connect-time full state: the latest spine seq plus the folded RunViews.

    ``since_seq`` lets a caller fold only the tail of the spine, but the returned
    ``seq`` is always the true global latest so the client's cursor starts at the
    real head of the log.
    """
    return {
        "seq": spine_db.latest_seq(),
        "views": projection.build_view_from_spine(since_seq),
    }


def deltas_since(cursor: int) -> List[Dict[str, Any]]:
    """New durable events with ``seq`` strictly greater than ``cursor``."""
    return spine_db.tail_since(cursor)


def resume(cursor: int, ring: RingBuffer) -> List[Dict[str, Any]]:
    """Replay events after ``cursor`` for a reconnecting client (Last-Event-ID).

    Fast path: if the ring still retains everything past ``cursor``, serve from
    memory. Otherwise (cursor predates the ring, or the ring is empty) fall back
    to the durable spine, which is the real backing log and can always replay.
    """
    if ring.covers(cursor):
        return ring.events_since(cursor)
    return spine_db.tail_since(cursor)


def _frame(event_name: str, seq: int, data: Dict[str, Any]) -> str:
    """Assemble one SSE wire frame. ``data`` is JSON-encoded on the data line."""
    return (
        f"id: {seq}\n"
        f"event: {event_name}\n"
        f"data: {json.dumps(data, default=str)}\n"
        f"\n"
    )


def format_sse_frame(event: Dict[str, Any]) -> str:
    """Render one spine event as an SSE frame.

    The ``id:`` line carries the durable seq (the resume cursor). The ``data:``
    line carries the event payload plus envelope meta so the client can update
    its view without a second lookup.
    """
    seq = event["seq"]
    data = {
        "seq": seq,
        "run_id": event.get("run_id"),
        "parent_run_id": event.get("parent_run_id"),
        "source": event.get("source"),
        "type": event.get("type"),
        "agent_id": event.get("agent_id"),
        "team_id": event.get("team_id"),
        "ts": event.get("ts"),
        "payload": event.get("payload") or {},
    }
    return _frame(event.get("type", "message"), seq, data)


def format_snapshot_frame(snap: Dict[str, Any]) -> str:
    """Render a connect-time snapshot as an ``event: snapshot`` SSE frame."""
    seq = snap.get("seq", 0)
    data = {"seq": seq, "views": snap.get("views", [])}
    return _frame("snapshot", seq, data)
