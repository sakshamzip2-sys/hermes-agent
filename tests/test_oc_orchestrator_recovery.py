"""Tests for intent-then-execute recovery (Feature C).

Proves the orchestrator takes care of failures: a failed task is recovered by a
single idempotent retry that routes through the cap ledger; concurrent
reconcilers do not double-recover; the attempt cap stops a stubborn task; and a
crash between deciding to spawn and actually spawning is re-executed exactly once
(no double-spawn, no abandon).

Stdlib + pytest only, real SQLite, no mocks of the recovery logic (the spawn is
injected so no real subprocess is needed).
"""

from __future__ import annotations

import pytest

from plugins.oc_orchestrator import caps, recovery
from plugins.oc_orchestrator import db as odb


def _reset():
    for attr in ("conn", "path"):
        if hasattr(odb._local, attr):
            try:
                if attr == "conn" and odb._local.conn is not None:
                    odb._local.conn.close()
            except Exception:
                pass
            delattr(odb._local, attr)


@pytest.fixture()
def orch_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_ORCHESTRATOR_DB", str(tmp_path / "oc_orchestrator.db"))
    _reset()
    yield
    _reset()


class FakeSpawner:
    """Records calls; can be told to raise the first N times to simulate a crash
    between intent creation and the spawn flip."""

    def __init__(self, fail_times: int = 0):
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, *, attempt_no, intent_id):
        self.calls.append((attempt_no, intent_id))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("simulated crash before flip")
        return f"child-{attempt_no}"


def _intent_state(conn, intent_id):
    row = conn.execute("SELECT state, child_id FROM spawn_intents WHERE id=?", (intent_id,)).fetchone()
    return row["state"], row["child_id"]


def test_failed_run_triggers_one_idempotent_retry(orch_db):
    with odb.connect() as conn:
        sp = FakeSpawner()
        r = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                      failure_seq=10, spawn_fn=sp, max_attempts=3)
        assert r.action == "retried"
        assert r.attempt_no == 1
        assert r.child_id == "child-1"
        assert len(sp.calls) == 1
        assert _intent_state(conn, r.intent_id) == ("launched", "child-1")
        assert recovery.active_reservation_count(conn, "t") == 1


def test_concurrent_reconcilers_recover_once(orch_db):
    with odb.connect() as conn:
        sp = FakeSpawner()
        r1 = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                       failure_seq=5, spawn_fn=sp, max_attempts=3)
        r2 = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                       failure_seq=5, spawn_fn=sp, max_attempts=3)
        assert r1.action == "retried"
        assert r2.action == "already_claimed"
        assert len(sp.calls) == 1
        assert recovery.active_reservation_count(conn, "t") == 1


def test_attempt_cap_exhausts(orch_db):
    with odb.connect() as conn:
        sp = FakeSpawner()
        r1 = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                       failure_seq=1, spawn_fn=sp, max_attempts=2)
        r2 = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                       failure_seq=2, spawn_fn=sp, max_attempts=2)
        r3 = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                       failure_seq=3, spawn_fn=sp, max_attempts=2)
        assert r1.action == "retried" and r2.action == "retried"
        assert r3.action == "exhausted"
        assert len(sp.calls) == 2  # no spawn on exhaustion


def test_recovery_respects_caps(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_concurrent": 0})
        sp = FakeSpawner()
        r = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                      failure_seq=1, spawn_fn=sp, max_attempts=3)
        assert r.action == "refused"
        assert r.detail == "concurrent"
        assert len(sp.calls) == 0  # never spawned when the cap refuses


def test_crash_between_intent_and_execute_is_re_executed_once(orch_db):
    with odb.connect() as conn:
        sp = FakeSpawner(fail_times=1)  # the spawn during attempt_recovery crashes
        r = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                      failure_seq=9, spawn_fn=sp, max_attempts=3)
        # The decision committed (intent pending, slot reserved) but the spawn
        # crashed before the flip.
        assert r.action == "retried"
        assert r.child_id is None
        assert _intent_state(conn, r.intent_id) == ("pending", None)
        assert recovery.active_reservation_count(conn, "t") == 1

        # A reconcile tick re-executes the pending intent exactly once.
        results = recovery.reconcile_intents(conn, sp)
        assert len(results) == 1
        assert _intent_state(conn, r.intent_id) == ("launched", "child-1")
        # spawn_fn called twice total (1 crash + 1 success), no double reservation.
        assert len(sp.calls) == 2
        assert recovery.active_reservation_count(conn, "t") == 1
