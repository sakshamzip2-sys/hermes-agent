"""Brutal adversarial probes for the cockpit projection fold and SSE tailer.

Targets:
  * plugins/parallel_view/projection.py  (pure fold over spine events)
  * plugins/oc_runs/sse_tailer.py        (RingBuffer + resume + SSE frames)

Each probe asserts the ROBUST behavior; if the module has a bug the assertion
fails and the bug is exposed. We do NOT modify any source. Some probes that
emit through the durable spine use the standard isolation pattern (tmp DB +
thread-local reset); the pure-fold probes feed dicts directly and need no DB.
"""

from __future__ import annotations

import json

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events as ev
from plugins.oc_runs import sse_tailer
from plugins.parallel_view import projection


# --------------------------------------------------------------------------- #
# isolation                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fresh_spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
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


def _raw(seq, run_id, etype, *, source="agents", payload=None, **extra):
    """A raw event dict as the fold consumes it (no DB)."""
    e = {"seq": seq, "run_id": run_id, "type": etype, "source": source}
    if payload is not None:
        e["payload"] = payload
    e.update(extra)
    return e


# --------------------------------------------------------------------------- #
# PROBE 1: unknown type + unknown source never yields 'running'                #
# --------------------------------------------------------------------------- #
def test_unknown_type_and_source_does_not_crash_or_go_running():
    """A future event kind from a future source is DATA, not error. The fold
    must not crash and must NOT promote the run to 'running' (guardrail 9:
    open vocab; unmapped stays pending/unknown)."""
    events = [
        _raw(1, "rX", "run.created", source="agents", payload={"name": "x"}),
        _raw(2, "rX", "quantum.entangled", source="time_travel_engine_v9",
             payload={"status": "running", "annotation": "slow"}),
        _raw(3, "rX", "totally.made.up", source="nope"),
    ]
    view = projection.fold_run("rX", events)
    assert view["state"] == "pending", (
        f"unknown-type fold leaked into state={view['state']!r}; an unmapped "
        "event type must never change the run state"
    )
    assert view["state"] != "running"
    # last_seq still tracked across the unknown events.
    assert view["last_seq"] == 3


# --------------------------------------------------------------------------- #
# PROBE 2: run.status with an unmapped status -> unknown, never 'running'      #
# --------------------------------------------------------------------------- #
def test_unmapped_status_normalizes_to_unknown_not_running():
    events = [
        _raw(1, "rS", "run.created", payload={"name": "s"}),
        _raw(2, "rS", "run.status", payload={"status": "frobnicating"}),
    ]
    view = projection.fold_run("rS", events)
    assert view["state"] == "unknown", (
        f"a status value outside the native map must normalize to 'unknown', "
        f"got {view['state']!r}"
    )
    assert view["state"] != "running"


def test_normalize_state_none_and_garbage():
    assert projection.normalize_state(None) == "unknown"
    assert projection.normalize_state("") == "unknown"
    assert projection.normalize_state("RUNNING") == "running"  # case-insensitive
    assert projection.normalize_state("not_a_state") == "unknown"


# --------------------------------------------------------------------------- #
# PROBE 3: a run with only progress events folds to a sane (non-running) state #
# --------------------------------------------------------------------------- #
def test_only_progress_events_stays_pending():
    """run.progress/heartbeat/tool.* keep state as-is. With no created/status
    event the run should remain at the pending default, NOT silently 'running'
    nor crash."""
    events = [
        _raw(1, "rP", "run.progress", payload={"pct": 10}),
        _raw(2, "rP", "heartbeat", payload={}),
        _raw(3, "rP", "tool.started", payload={"tool": "x"}),
        _raw(4, "rP", "tool.completed", payload={"tool": "x"}),
    ]
    view = projection.fold_run("rP", events)
    assert view["state"] == "pending", (
        f"progress-only run folded to {view['state']!r}; should stay 'pending'"
    )
    assert view["last_seq"] == 4


# --------------------------------------------------------------------------- #
# PROBE 4: conflicting terminals — stalled then completed (higher seq) wins    #
# --------------------------------------------------------------------------- #
def test_conflicting_terminals_higher_seq_completed_wins():
    """A reconciler writes run.stalled (low seq) while the worker was merely
    slow; the real engine later writes run.completed (high seq). The fold must
    end in 'completed' because the last terminal by seq wins. Crucially this
    must hold even when events are fed OUT of seq order (fold sorts)."""
    events = [
        _raw(1, "rT", "run.created", payload={"name": "t"}),
        _raw(3, "rT", "run.completed", payload={"reason": "ok"}),  # higher seq
        _raw(2, "rT", "run.stalled", payload={"reason": "watchdog"}),  # lower seq, fed first-ish
    ]
    view = projection.fold_run("rT", events)
    assert view["state"] == "completed", (
        f"completed (seq 3) must supersede stalled (seq 2); got {view['state']!r}"
    )
    # completed clears the slow flag.
    assert view["slow"] is False


def test_completed_then_stalled_lower_seq_does_not_override():
    """Reverse safety check: an older stalled must not clobber a newer completed
    regardless of insertion order into the iterable."""
    events = [
        _raw(2, "rT2", "run.stalled", payload={"reason": "w"}),
        _raw(1, "rT2", "run.created", payload={"name": "t"}),
        _raw(3, "rT2", "run.completed", payload={"reason": "ok"}),
    ]
    view = projection.fold_run("rT2", events)
    assert view["state"] == "completed"


# --------------------------------------------------------------------------- #
# PROBE 5: RingBuffer eviction beyond capacity evicts the oldest               #
# --------------------------------------------------------------------------- #
def test_ring_buffer_evicts_oldest_beyond_capacity():
    ring = sse_tailer.RingBuffer(capacity=3)
    for s in range(1, 6):  # seqs 1..5, capacity 3 -> keep 3,4,5
        ring.append({"seq": s})
    assert ring.oldest_seq() == 3, f"oldest after eviction should be 3, got {ring.oldest_seq()}"
    assert ring.latest_seq() == 5
    # events_since must reflect only retained events.
    assert [e["seq"] for e in ring.events_since(0)] == [3, 4, 5]
    assert [e["seq"] for e in ring.events_since(4)] == [5]


def test_ring_buffer_rejects_nonpositive_capacity():
    with pytest.raises(ValueError):
        sse_tailer.RingBuffer(capacity=0)
    with pytest.raises(ValueError):
        sse_tailer.RingBuffer(capacity=-5)


# --------------------------------------------------------------------------- #
# PROBE 6: resume with a cursor older than the ring -> full spine history      #
# --------------------------------------------------------------------------- #
def test_resume_cursor_older_than_ring_falls_back_to_full_spine(fresh_spine):
    """Emit 10 durable events. Build a tiny ring that only retains the last few.
    A client resuming from cursor=0 (predates the ring) must get the COMPLETE
    post-cursor history from the spine, not the truncated ring window. Losing
    events here is silent data loss in the cockpit."""
    emitted = []
    for i in range(10):
        emitted.append(_emit(f"run-{i}", ev.RUN_CREATED, payload={"name": str(i)})["seq"])

    # Ring only holds the last 3 (seqs 8,9,10).
    ring = sse_tailer.RingBuffer(capacity=3)
    for s in spine_db.tail_since(emitted[6]):  # seqs > 7th-emitted
        ring.append(s)
    assert ring.oldest_seq() > emitted[0], "precondition: ring must NOT cover cursor 0"
    assert not ring.covers(0), "cursor 0 predates the ring; covers() must be False"

    replayed = sse_tailer.resume(0, ring)
    assert [e["seq"] for e in replayed] == emitted, (
        "resume from a cursor older than the ring must replay the FULL spine "
        f"history; got {[e['seq'] for e in replayed]} expected {emitted}"
    )


def test_resume_hot_path_serves_from_ring_when_covered(fresh_spine):
    """When the ring fully covers the cursor it should serve from memory and the
    boundary must be exact: events_since is strictly-greater-than the cursor."""
    for i in range(5):
        _emit(f"hp-{i}", ev.RUN_CREATED, payload={"name": str(i)})
    all_ev = spine_db.tail_since(0)
    ring = sse_tailer.RingBuffer(capacity=10)
    for e in all_ev:
        ring.append(e)
    cursor = all_ev[1]["seq"]  # resume after the 2nd event
    assert ring.covers(cursor)
    replayed = sse_tailer.resume(cursor, ring)
    assert all(e["seq"] > cursor for e in replayed), "resume must be strictly > cursor"
    assert [e["seq"] for e in replayed] == [e["seq"] for e in all_ev if e["seq"] > cursor]


def test_resume_empty_ring_falls_back_to_spine(fresh_spine):
    for i in range(4):
        _emit(f"er-{i}", ev.RUN_CREATED, payload={"name": str(i)})
    ring = sse_tailer.RingBuffer(capacity=8)  # never appended to
    assert not ring.covers(0)
    replayed = sse_tailer.resume(0, ring)
    assert len(replayed) == 4, "empty ring must fall back to the durable spine"


def test_ring_covers_boundary_off_by_one(fresh_spine):
    """The contract: covers(cursor) is True iff cursor >= oldest_seq - 1, so the
    first missed event (oldest retained) is still served. Probe the exact
    boundary so an off-by-one in covers() can't silently drop the oldest event."""
    ring = sse_tailer.RingBuffer(capacity=3)
    for s in (5, 6, 7):
        ring.append({"seq": s})
    # oldest is 5. cursor=4 means client has up to 4, missed 5,6,7 -> ring covers.
    assert ring.covers(4) is True
    assert ring.events_since(4) == [{"seq": 5}, {"seq": 6}, {"seq": 7}]
    # cursor=3 means client missed 4 (which is NOT in the ring) -> must NOT claim coverage.
    assert ring.covers(3) is False, (
        "cursor 3 missed seq 4 which the ring evicted; covers() must be False so "
        "the caller falls back to the spine and does not drop seq 4"
    )


# --------------------------------------------------------------------------- #
# PROBE 7: SSE frames parseable for None / empty / missing payload             #
# --------------------------------------------------------------------------- #
def _parse_sse(frame: str) -> dict:
    """Parse an SSE frame into {id, event, data(dict)}; assert structure."""
    lines = frame.split("\n")
    out = {}
    for line in lines:
        if line.startswith("id: "):
            out["id"] = line[4:]
        elif line.startswith("event: "):
            out["event"] = line[7:]
        elif line.startswith("data: "):
            out["data"] = json.loads(line[6:])
    assert frame.endswith("\n\n"), "an SSE frame must terminate with a blank line"
    return out


def test_format_frame_none_payload_is_valid_json():
    event = {
        "seq": 42,
        "run_id": "r",
        "type": "run.progress",
        "source": "agents",
        "payload": None,  # explicitly None
    }
    parsed = _parse_sse(sse_tailer.format_sse_frame(event))
    assert parsed["id"] == "42"
    assert parsed["event"] == "run.progress"
    assert parsed["data"]["payload"] == {}, "None payload must serialize as {}"
    assert parsed["data"]["seq"] == 42


def test_format_frame_missing_payload_and_type():
    """A degenerate event missing payload AND type must still produce a parseable
    frame with the 'message' default event name and not raise."""
    event = {"seq": 7, "run_id": "r"}
    parsed = _parse_sse(sse_tailer.format_sse_frame(event))
    assert parsed["event"] == "message"
    assert parsed["data"]["payload"] == {}
    assert parsed["data"]["type"] is None


def test_format_frame_payload_with_newline_does_not_break_framing():
    """A payload string containing a newline must NOT inject extra SSE data lines
    (json.dumps escapes it). If it leaked raw it would corrupt the stream."""
    event = {
        "seq": 9,
        "run_id": "r",
        "type": "team.message",
        "source": "teams",
        "payload": {"text": "line1\nline2\ndata: injected"},
    }
    frame = sse_tailer.format_sse_frame(event)
    # Exactly one 'data: ' line in the frame.
    data_lines = [ln for ln in frame.split("\n") if ln.startswith("data: ")]
    assert len(data_lines) == 1, (
        f"newline/data: in payload leaked into the SSE wire as {len(data_lines)} "
        "data lines; framing is corrupted"
    )
    parsed = _parse_sse(frame)
    assert parsed["data"]["payload"]["text"] == "line1\nline2\ndata: injected"


def test_format_snapshot_frame_empty_and_missing():
    parsed = _parse_sse(sse_tailer.format_snapshot_frame({}))
    assert parsed["event"] == "snapshot"
    assert parsed["id"] == "0"
    assert parsed["data"]["views"] == []
    # with content
    parsed2 = _parse_sse(sse_tailer.format_snapshot_frame({"seq": 5, "views": [{"run_id": "x"}]}))
    assert parsed2["id"] == "5"
    assert parsed2["data"]["views"] == [{"run_id": "x"}]


# --------------------------------------------------------------------------- #
# PROBE 8: build_view groups multiple runs and sorts by last_seq               #
# --------------------------------------------------------------------------- #
def test_build_view_groups_and_orders_by_last_seq():
    events = [
        _raw(1, "a", "run.created", payload={"name": "A"}),
        _raw(2, "b", "run.created", payload={"name": "B"}),
        _raw(3, "a", "run.completed", payload={"reason": "ok"}),
        _raw(4, "b", "run.status", payload={"status": "working"}),
    ]
    views = projection.build_view(events)
    assert [v["run_id"] for v in views] == ["a", "b"], "views ordered by last_seq"
    by_id = {v["run_id"]: v for v in views}
    assert by_id["a"]["state"] == "completed"
    assert by_id["b"]["state"] == "running"  # 'working' -> running


def test_fold_missing_seq_key_is_a_hard_error_not_silent():
    """An event missing the mandatory seq cannot be ordered; the fold must NOT
    silently mis-order or pretend success. We assert it raises (KeyError) so a
    malformed producer is caught loudly rather than corrupting ordering."""
    events = [_raw(1, "z", "run.created", payload={"name": "z"}), {"run_id": "z", "type": "heartbeat"}]
    with pytest.raises(KeyError):
        projection.fold_run("z", events)
