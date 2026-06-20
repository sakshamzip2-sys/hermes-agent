"""aiohttp SSE handler for the live parallel-agents run stream.

A thin wrapper over the tested sse_tailer core, kept out of the giant api_server
class so it is HTTP-testable in isolation with aiohttp's test client:

- A fresh client (no Last-Event-ID) gets a full ``snapshot`` frame, then live
  delta frames as run-state changes land on the spine.
- A reconnecting client (Last-Event-ID, or ?cursor=) gets the deltas since its
  cursor, replayed from the durable spine (so a gateway restart loses nothing).
- ?once=1 sends the initial frames then closes (used by tests and one-shot polls).

Each frame carries the durable seq in its ``id:`` line, so the browser's
EventSource sets Last-Event-ID automatically on reconnect.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from . import sse_tailer

SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}
POLL_INTERVAL = 2.0


def _parse_cursor(request) -> Optional[int]:
    raw = request.headers.get("Last-Event-ID")
    if raw is None:
        raw = request.query.get("cursor")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def stream_events(request, *, extra_headers=None, once: bool = False,
                        poll_interval: float = POLL_INTERVAL):
    """Stream the parallel-agents run view as SSE. Returns the StreamResponse."""
    from aiohttp import web

    headers = dict(SSE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    if request.query.get("once") in ("1", "true", "yes"):
        once = True

    cursor = _parse_cursor(request)
    resuming = cursor is not None

    response = web.StreamResponse(status=200, headers=headers)
    await response.prepare(request)

    try:
        if not resuming:
            snap = sse_tailer.snapshot()
            await response.write(sse_tailer.format_snapshot_frame(snap).encode())
            cursor = snap["seq"]
        else:
            for event in sse_tailer.deltas_since(cursor):
                await response.write(sse_tailer.format_sse_frame(event).encode())
                cursor = event["seq"]

        while not once:
            await asyncio.sleep(poll_interval)
            for event in sse_tailer.deltas_since(cursor):
                await response.write(sse_tailer.format_sse_frame(event).encode())
                cursor = event["seq"]
    except (ConnectionResetError, asyncio.CancelledError):
        pass  # client disconnected; stop streaming
    return response
