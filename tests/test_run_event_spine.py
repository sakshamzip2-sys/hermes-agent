"""Tests for the oc_runs event spine (Feature B durable run-state log).

Stdlib + pytest only, no network, no LLM. The spine DB is isolated to a tmp
file. These exercise the real SQLite append-only log and snapshot cache: append
ordering and monotonic seq, idempotent emit via dedupe_key, tail-since cursor
semantics, the frozen versioned envelope, open type/source vocabulary, snapshot
upsert, and durable restart-replay (seq survives a connection drop).
"""

from __future__ import annotations

import pytest

from plugins.oc_runs import db, events


def _reset_local() -> None:
    for attr in ("conn", "path"):
        if hasattr(db._local, attr):
            try:
                if attr == "conn" and db._local.conn is not None:
                    db._local.conn.close()
            except Exception:
                pass
            delattr(db._local, attr)


@pytest.fixture()
def runs_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_local()
    yield
    _reset_local()


# --------------------------------------------------------------------------- #
# build_event envelope
# --------------------------------------------------------------------------- #

def test_build_event_envelope_has_required_fields():
    ev = events.build_event(
        "agents:abc",
        events.RUN_CREATED,
        source=events.SOURCE_AGENTS,
        payload={"name": "demo"},
    )
    assert ev["run_id"] == "agents:abc"
    assert ev["type"] == events.RUN_CREATED
    assert ev["source"] == events.SOURCE_AGENTS
    assert ev["schema_version"] == events.SCHEMA_VERSION
    assert isinstance(ev["ts"], float) and ev["ts"] > 0
    assert ev["payload"] == {"name": "demo"}
    # seq is assigned at append, never by the builder.
    assert "seq" not in ev


# --------------------------------------------------------------------------- #
# append + tail
# --------------------------------------------------------------------------- #

def test_append_and_tail_returns_events_with_monotonic_seq(runs_db):
    s1 = db.append_event(events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS))
    s2 = db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                            payload={"status": "running"}))
    assert s2 > s1
    rows = db.tail_since(0)
    assert [r["seq"] for r in rows] == [s1, s2]
    assert rows[0]["type"] == events.RUN_CREATED
    assert rows[1]["payload"] == {"status": "running"}
    assert db.latest_seq() == s2


def test_tail_since_returns_only_newer_events(runs_db):
    s1 = db.append_event(events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS))
    s2 = db.append_event(events.build_event("agents:a", events.RUN_COMPLETED, source=events.SOURCE_AGENTS))
    rows = db.tail_since(s1)
    assert [r["seq"] for r in rows] == [s2]


def test_schema_version_stamped_on_every_row(runs_db):
    db.append_event(events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS))
    db.append_event(events.build_event("teams:t", events.TEAM_MESSAGE, source=events.SOURCE_TEAMS))
    rows = db.tail_since(0)
    assert all(r["schema_version"] == events.SCHEMA_VERSION for r in rows)


# --------------------------------------------------------------------------- #
# idempotent emit (guardrail 5)
# --------------------------------------------------------------------------- #

def test_idempotent_emit_with_same_dedupe_key(runs_db):
    ev = events.build_event("agents:a", events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                            dedupe_key="terminal", payload={"reason": "process_died"})
    s1 = db.append_event(ev)
    # Re-emitting the identical terminal event (e.g. two reconcilers) must not
    # create a second row and must return the original seq.
    s2 = db.append_event(events.build_event("agents:a", events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                                            dedupe_key="terminal", payload={"reason": "process_died"}))
    assert s1 == s2
    rows = db.tail_since(0)
    assert len(rows) == 1


def test_events_without_dedupe_key_always_insert(runs_db):
    db.append_event(events.build_event("agents:a", events.HEARTBEAT, source=events.SOURCE_AGENTS))
    db.append_event(events.build_event("agents:a", events.HEARTBEAT, source=events.SOURCE_AGENTS))
    rows = db.tail_since(0)
    assert len(rows) == 2  # NULL dedupe_key rows are distinct


def test_dedupe_is_scoped_per_run_id(runs_db):
    # Same dedupe_key under different run_ids must both insert.
    db.append_event(events.build_event("agents:a", events.RUN_COMPLETED, source=events.SOURCE_AGENTS,
                                       dedupe_key="terminal"))
    db.append_event(events.build_event("agents:b", events.RUN_COMPLETED, source=events.SOURCE_AGENTS,
                                       dedupe_key="terminal"))
    assert len(db.tail_since(0)) == 2


# --------------------------------------------------------------------------- #
# open vocabulary (guardrail 9: unknown types/sources are data, not errors)
# --------------------------------------------------------------------------- #

def test_unknown_type_and_source_are_stored(runs_db):
    db.append_event(events.build_event("x:1", "some.future.event", source="future_source",
                                       payload={"k": "v"}))
    rows = db.tail_since(0)
    assert rows[0]["type"] == "some.future.event"
    assert rows[0]["source"] == "future_source"
    assert rows[0]["payload"] == {"k": "v"}


# --------------------------------------------------------------------------- #
# snapshot cache (pure-fold projection, never a second source of truth)
# --------------------------------------------------------------------------- #

def test_snapshot_upsert_and_get(runs_db):
    db.upsert_snapshot("agents:a", last_seq=5, state={"status": "running", "name": "demo"})
    snap = db.get_snapshot("agents:a")
    assert snap["last_seq"] == 5
    assert snap["state"]["status"] == "running"
    # upsert replaces, never duplicates
    db.upsert_snapshot("agents:a", last_seq=9, state={"status": "completed"})
    snap = db.get_snapshot("agents:a")
    assert snap["last_seq"] == 9
    assert snap["state"]["status"] == "completed"
    assert len(db.list_snapshots()) == 1


# --------------------------------------------------------------------------- #
# durability / restart-replay (guardrail 5)
# --------------------------------------------------------------------------- #

def test_seq_persists_and_replays_across_connection_drop(runs_db):
    s1 = db.append_event(events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS))
    s2 = db.append_event(events.build_event("agents:a", events.RUN_COMPLETED, source=events.SOURCE_AGENTS))
    # Simulate a gateway restart: drop the cached connection, reopen.
    _reset_local()
    rows = db.tail_since(0)
    assert [r["seq"] for r in rows] == [s1, s2]
    # A new append continues the monotonic sequence, never reusing a seq.
    s3 = db.append_event(events.build_event("agents:a", events.RUN_PROGRESS, source=events.SOURCE_AGENTS))
    assert s3 > s2
    # Last-Event-ID replay: a client resuming from s1 gets only s2, s3.
    assert [r["seq"] for r in db.tail_since(s1)] == [s2, s3]
