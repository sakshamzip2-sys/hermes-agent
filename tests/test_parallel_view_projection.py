"""Tests for the spine -> RunView projection fold (Feature B).

Proves the fold normalizes state correctly, that a real terminal supersedes a
reconciler stalled by seq, that an unmapped status becomes 'unknown' (never
'running', so a schema gap cannot hide a failure), and that slow is an annotation
not a terminal. Real SQLite spine, no mocks.
"""

from __future__ import annotations

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events
from plugins.parallel_view import projection


def _reset():
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            try:
                if attr == "conn" and spine_db._local.conn is not None:
                    spine_db._local.conn.close()
            except Exception:
                pass
            delattr(spine_db._local, attr)


@pytest.fixture()
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset()
    yield
    _reset()


def _view_for(run_id):
    return next(v for v in projection.build_view_from_spine() if v["run_id"] == run_id)


def test_fold_lifecycle_to_completed(spine):
    spine_db.append_event(events.build_event("agents:a", events.RUN_CREATED, source=events.SOURCE_AGENTS,
                                             payload={"name": "demo"}))
    spine_db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                             payload={"status": "running"}))
    spine_db.append_event(events.build_event("agents:a", events.RUN_COMPLETED, source=events.SOURCE_AGENTS,
                                             payload={"status": "completed"}))
    v = _view_for("agents:a")
    assert v["state"] == "completed"
    assert v["title"] == "demo"
    assert v["source"] == "agents"


def test_fold_failure_carries_reason(spine):
    spine_db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                             payload={"status": "running"}))
    spine_db.append_event(events.build_event("agents:a", events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                                             payload={"reason": "process_died"}))
    v = _view_for("agents:a")
    assert v["state"] == "failed"
    assert v["reason"] == "process_died"


def test_real_completed_supersedes_reconciler_stalled(spine):
    # Reconciler flags stalled (lower seq); the worker was merely slow and later
    # emits a real completed (higher seq), which must win.
    spine_db.append_event(events.build_event("agents:a", events.RUN_STALLED, source=events.SOURCE_RECONCILER,
                                             payload={"reason": "no_heartbeat"}))
    spine_db.append_event(events.build_event("agents:a", events.RUN_COMPLETED, source=events.SOURCE_AGENTS,
                                             payload={"status": "completed"}))
    assert _view_for("agents:a")["state"] == "completed"


def test_unmapped_status_is_unknown_not_running(spine):
    spine_db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                             payload={"status": "some_future_state"}))
    assert _view_for("agents:a")["state"] == "unknown"


def test_slow_is_annotation_not_terminal(spine):
    spine_db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_AGENTS,
                                             payload={"status": "running"}))
    spine_db.append_event(events.build_event("agents:a", events.RUN_STATUS, source=events.SOURCE_RECONCILER,
                                             payload={"display": "running", "annotation": "slow"}))
    v = _view_for("agents:a")
    assert v["state"] == "running"
    assert v["slow"] is True


def test_build_view_groups_runs_and_tracks_parent(spine):
    spine_db.append_event(events.build_event("teams:t1", events.RUN_CREATED, source=events.SOURCE_TEAMS))
    spine_db.append_event(events.build_event("teams:t1:alice", events.RUN_CREATED, source=events.SOURCE_TEAMS,
                                             parent_run_id="teams:t1"))
    views = projection.build_view_from_spine()
    by_id = {v["run_id"]: v for v in views}
    assert set(by_id) == {"teams:t1", "teams:t1:alice"}
    assert by_id["teams:t1:alice"]["parent_run_id"] == "teams:t1"
