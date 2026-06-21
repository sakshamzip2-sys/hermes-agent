"""Tests for the resumable SSE tailer core (plugins/oc_runs/sse_tailer.py).

These exercise real behavior against the durable spine (oc_runs.db) using the
standard test-isolation pattern: point HERMES_OC_RUNS_DB at a tmp file and reset
the spine module's thread-local connection so each test gets a clean DB.
"""

from __future__ import annotations

import json

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events as ev
from plugins.oc_runs import sse_tailer


@pytest.fixture()
def fresh_spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    # Reset the thread-local connection so connect() reopens against the tmp DB.
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            delattr(spine_db._local, attr)
    yield
    conn = getattr(spine_db._local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            delattr(spine_db._local, attr)


def _emit(run_id, event_type, *, source=ev.SOURCE_AGENTS, payload=None, dedupe_key=None):
    event = ev.build_event(
        run_id, event_type, source=source, payload=payload, dedupe_key=dedupe_key
    )
    seq = spine_db.append_event(event)
    event["seq"] = seq
    return event


def test_snapshot_returns_current_views_and_seq(fresh_spine):
    _emit("r1", ev.RUN_CREATED, payload={"name": "Build the thing"})
    _emit("r1", ev.RUN_STATUS, payload={"status": "running"})
    last = _emit("r2", ev.RUN_CREATED, payload={"name": "Other"})["seq"]

    snap = sse_tailer.snapshot()

    assert snap["seq"] == last == spine_db.latest_seq()
    by_id = {v["run_id"]: v for v in snap["views"]}
    assert set(by_id) == {"r1", "r2"}
    assert by_id["r1"]["state"] == "running"
    assert by_id["r1"]["title"] == "Build the thing"


def test_deltas_since_excludes_cursor_and_below(fresh_spine):
    e1 = _emit("r1", ev.RUN_CREATED, payload={"name": "a"})
    e2 = _emit("r1", ev.RUN_STATUS, payload={"status": "running"})
    e3 = _emit("r1", ev.RUN_COMPLETED, payload={"reason": "done"})

    out = sse_tailer.deltas_since(e1["seq"])

    seqs = [e["seq"] for e in out]
    assert e1["seq"] not in seqs
    assert seqs == [e2["seq"], e3["seq"]]


def test_ring_serves_recent_resume(fresh_spine):
    ring = sse_tailer.RingBuffer(capacity=8)
    events = [
        _emit("r1", ev.RUN_PROGRESS, payload={"i": i})
        for i in range(5)
    ]
    for e in events:
        ring.append(e)

    cursor = events[1]["seq"]
    served = sse_tailer.resume(cursor, ring)

    assert [e["seq"] for e in served] == [events[2]["seq"], events[3]["seq"], events[4]["seq"]]
    # Confirm it came from the ring (no extra spine read needed): the in-memory
    # ring's own events_since must agree.
    assert ring.events_since(cursor) == served


def test_cursor_older_than_ring_falls_back_to_spine(fresh_spine):
    # Emit a long history to the durable spine.
    history = [_emit("r1", ev.RUN_PROGRESS, payload={"i": i}) for i in range(10)]

    # A small ring that only retained the most recent few events.
    ring = sse_tailer.RingBuffer(capacity=3)
    for e in history[-3:]:
        ring.append(e)

    # Cursor predates everything in the ring -> must fall back to the spine and
    # still return every event after the cursor (not just the ring's tail).
    cursor = history[1]["seq"]
    assert ring.oldest_seq() > cursor  # precondition: cursor really predates ring

    served = sse_tailer.resume(cursor, ring)

    assert [e["seq"] for e in served] == [e["seq"] for e in history[2:]]


def test_resume_empty_ring_falls_back_to_spine(fresh_spine):
    history = [_emit("r1", ev.RUN_PROGRESS, payload={"i": i}) for i in range(4)]
    ring = sse_tailer.RingBuffer(capacity=8)  # never populated

    served = sse_tailer.resume(history[0]["seq"], ring)

    assert [e["seq"] for e in served] == [e["seq"] for e in history[1:]]


def test_ring_capacity_evicts_oldest(fresh_spine):
    ring = sse_tailer.RingBuffer(capacity=3)
    events = [_emit("r1", ev.RUN_PROGRESS, payload={"i": i}) for i in range(5)]
    for e in events:
        ring.append(e)

    # Only the last 3 survive.
    assert [e["seq"] for e in ring.events_since(0)] == [
        events[2]["seq"],
        events[3]["seq"],
        events[4]["seq"],
    ]
    assert ring.oldest_seq() == events[2]["seq"]


def test_default_ring_capacity_is_1024(fresh_spine):
    ring = sse_tailer.RingBuffer()
    assert ring.capacity == 1024


def test_format_sse_frame_has_seq_id_and_is_parseable(fresh_spine):
    e = _emit("r1", ev.RUN_STATUS, payload={"status": "running", "n": 7})

    frame = sse_tailer.format_sse_frame(e)

    lines = frame.split("\n")
    assert frame.endswith("\n\n")
    assert f"id: {e['seq']}" in lines
    assert f"event: {ev.RUN_STATUS}" in lines

    data_line = next(line for line in lines if line.startswith("data: "))
    parsed = json.loads(data_line[len("data: "):])
    # The data payload carries the run payload plus envelope meta.
    assert parsed["seq"] == e["seq"]
    assert parsed["run_id"] == "r1"
    assert parsed["type"] == ev.RUN_STATUS
    assert parsed["payload"]["status"] == "running"
    assert parsed["payload"]["n"] == 7


def test_format_snapshot_frame_is_snapshot_event(fresh_spine):
    _emit("r1", ev.RUN_CREATED, payload={"name": "x"})
    snap = sse_tailer.snapshot()

    frame = sse_tailer.format_snapshot_frame(snap)

    lines = frame.split("\n")
    assert frame.endswith("\n\n")
    assert "event: snapshot" in lines
    assert f"id: {snap['seq']}" in lines
    data_line = next(line for line in lines if line.startswith("data: "))
    parsed = json.loads(data_line[len("data: "):])
    assert parsed["seq"] == snap["seq"]
    assert isinstance(parsed["views"], list)
    assert parsed["views"][0]["run_id"] == "r1"
