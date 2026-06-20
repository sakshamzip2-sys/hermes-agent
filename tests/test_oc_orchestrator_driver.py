"""Tests for the team driver loop: stall detection, bounded nudges, DAG events.

The decision (`tick`) is a pure function; `apply_actions` emits cache-safe inbox
nudges and spine events. Real SQLite spine, no mocks.
"""

from __future__ import annotations

import pytest

from plugins.oc_orchestrator.driver import (
    DriverConfig,
    Task,
    Teammate,
    apply_actions,
    tick,
)
from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events

CFG = DriverConfig(idle_window=60.0, max_nudges=3)


def _kinds(actions):
    return sorted((a.kind, a.target) for a in actions)


def test_idle_teammate_is_nudged():
    tm = Teammate("alice", pid_alive=True, last_heartbeat_age=120.0, nudges_sent=0)
    actions = tick([tm], [], cfg=CFG)
    assert ("nudge", "alice") in _kinds(actions)


def test_active_teammate_not_nudged():
    tm = Teammate("alice", pid_alive=True, last_heartbeat_age=5.0, nudges_sent=0)
    assert tick([tm], [], cfg=CFG) == []


def test_dead_pid_teammate_not_nudged():
    # A dead worker is the reconciler's job, not the driver's.
    tm = Teammate("alice", pid_alive=False, last_heartbeat_age=999.0, nudges_sent=0)
    assert tick([tm], [], cfg=CFG) == []


def test_nudges_are_rate_limited_then_escalate_to_terminate():
    tm = Teammate("alice", pid_alive=True, last_heartbeat_age=120.0, nudges_sent=3)
    actions = tick([tm], [], cfg=CFG)
    kinds = [a.kind for a in actions]
    assert "terminate" in kinds and "nudge" not in kinds


def test_dead_dependency_blocks_pending_task():
    tasks = [
        Task("a", status="failed"),
        Task("b", status="pending", depends_on=["a"]),
    ]
    actions = tick([], tasks, cfg=CFG)
    assert ("block_dead_dep", "b") in _kinds(actions)


def test_deps_satisfied_emits_unblocked():
    tasks = [
        Task("a", status="completed"),
        Task("b", status="pending", depends_on=["a"], owner=""),
    ]
    actions = tick([], tasks, cfg=CFG)
    assert ("dep_unblocked", "b") in _kinds(actions)


def test_incomplete_deps_do_not_unblock():
    tasks = [
        Task("a", status="in_progress"),
        Task("b", status="pending", depends_on=["a"]),
    ]
    actions = tick([], tasks, cfg=CFG)
    assert all(a.kind != "dep_unblocked" for a in actions)


# --------------------------------------------------------------------------- #
# apply_actions against the real spine
# --------------------------------------------------------------------------- #

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


def test_apply_actions_nudges_and_emits_spine_events(spine):
    nudged = []
    actions = tick(
        [Teammate("alice", pid_alive=True, last_heartbeat_age=120.0, nudges_sent=0)],
        [Task("a", status="completed"), Task("b", status="pending", depends_on=["a"])],
        cfg=CFG,
    )
    applied = apply_actions(actions, team_id="t1", enqueue_nudge=lambda name, text: nudged.append((name, text)))
    assert applied == len(actions)
    # alice was nudged via the inbox-only path
    assert any(n[0] == "alice" for n in nudged)
    types = [e["type"] for e in spine_db.tail_since(0)]
    assert "orchestrator.nudge" in types
    assert events.DEP_UNBLOCKED in types
