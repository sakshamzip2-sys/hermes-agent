"""Tests for the outbox + drainer (Feature B no-divergence guarantee).

An engine writes an outbox row INTO ITS OWN DB in the SAME transaction as its
state mutation; a single drainer reads undrained rows and appends them to the
spine, then marks them drained. The guarantee under test: an event is durably
queued atomically with the state change, the spine is the single idempotent
sink, and a drainer crash between spine-append and mark-drained replays without
duplicating (the synthesized per-row dedupe identity makes even keyless events
exactly-once into the spine).

Stdlib + pytest only, no network, no LLM. Real SQLite, no mocks.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import drainer, events, outbox


def _reset_spine_local():
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            try:
                if attr == "conn" and spine_db._local.conn is not None:
                    spine_db._local.conn.close()
            except Exception:
                pass
            delattr(spine_db._local, attr)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_spine_local()
    engine_path = str(tmp_path / "engine.db")

    @contextmanager
    def engine_connect():
        conn = sqlite3.connect(engine_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        outbox.ensure_outbox(conn)
        try:
            yield conn
        finally:
            conn.close()

    yield engine_connect
    _reset_spine_local()


def test_enqueue_then_fetch_then_mark_drained(env):
    engine_connect = env
    with engine_connect() as conn:
        eid = outbox.enqueue(conn, events.build_event(
            "agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
            payload={"status": "running"}))
        conn.commit()
        assert eid > 0
        undrained = outbox.fetch_undrained(conn)
        assert len(undrained) == 1
        assert undrained[0]["run_id"] == "agents:a"
        assert undrained[0]["outbox_id"] == eid
        outbox.mark_drained(conn, [eid])
        conn.commit()
        assert outbox.fetch_undrained(conn) == []


def test_drain_moves_events_into_spine_in_order(env):
    engine_connect = env
    with engine_connect() as conn:
        outbox.enqueue(conn, events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS))
        outbox.enqueue(conn, events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                                payload={"status": "running"}))
        conn.commit()
    n = drainer.drain(engine_connect)
    assert n == 2
    rows = spine_db.tail_since(0)
    assert [r["type"] for r in rows] == [events.RUN_CREATED, events.RUN_STATUS]
    assert rows[0]["seq"] < rows[1]["seq"]
    # all drained now
    with engine_connect() as conn:
        assert outbox.fetch_undrained(conn) == []


def test_drain_is_idempotent_on_crash_replay(env):
    """Simulate a drainer crash AFTER spine-append but BEFORE mark_drained: the
    rows stay undrained, a second drain must not create duplicate spine rows."""
    engine_connect = env
    with engine_connect() as conn:
        outbox.enqueue(conn, events.build_event("agents:a", events.RUN_PROGRESS, source=events.SOURCE_AGENTS,
                                                payload={"i": 1}))
        outbox.enqueue(conn, events.build_event("agents:a", events.RUN_PROGRESS, source=events.SOURCE_AGENTS,
                                                payload={"i": 2}))
        conn.commit()
    # First pass appends to the spine but we simulate the crash by re-appending
    # the SAME undrained rows again before they were marked drained.
    appended_1 = drainer.drain_append_only(engine_connect)  # append to spine, do NOT mark drained
    assert appended_1 == 2
    # crash here: rows are still undrained
    with engine_connect() as conn:
        assert len(outbox.fetch_undrained(conn)) == 2
    # recovery: a normal drain re-appends (deduped) and marks drained
    n = drainer.drain(engine_connect)
    assert n == 2
    rows = spine_db.tail_since(0)
    # exactly two spine rows, not four: keyless events got a stable per-row key
    assert len(rows) == 2
    with engine_connect() as conn:
        assert outbox.fetch_undrained(conn) == []


def test_drain_empty_is_noop(env):
    engine_connect = env
    assert drainer.drain(engine_connect) == 0
    assert spine_db.tail_since(0) == []


def test_explicit_dedupe_key_survives_drain(env):
    engine_connect = env
    with engine_connect() as conn:
        outbox.enqueue(conn, events.build_event("agents:a", events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                                                dedupe_key="terminal", payload={"reason": "x"}))
        conn.commit()
    drainer.drain(engine_connect)
    rows = spine_db.tail_since(0)
    assert rows[0]["dedupe_key"] == "terminal"
