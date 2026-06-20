"""Tests for the truth-under-failure reconciler (Feature B / guardrail 8).

The reconciler couples three independent signals so no single failure mode
yields a wrong status: pid liveness (pid + start-time, to kill the PID-reuse
false positive), a progress-coupled heartbeat, and an absolute wall-clock
timeout. The decision is a PURE function (`classify`) with `now` and the
`pid_alive` probe injected, so crash / hang / slow-but-healthy / timeout are all
deterministically testable without spawning processes. `reconcile_and_emit`
writes terminal events into the real spine and is idempotent.

Stdlib + pytest only, no network, no LLM, no mocks of the spine.
"""

from __future__ import annotations

import pytest

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events, reconciler
from plugins.oc_runs.reconciler import ReconcileConfig, RunLiveness

CFG = ReconcileConfig(progress_window=120.0, liveness_window=30.0, absolute_timeout=1800.0)
NOW = 10_000.0


def _alive(_pid, _start=None):
    return True


def _dead(_pid, _start=None):
    return False


def test_dead_pid_flips_to_failed_process_died():
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 5,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.action == "failed"
    assert v.reason == "process_died"
    assert v.event["type"] == events.RUN_FAILED


def test_alive_pid_no_beats_flips_to_stalled():
    # pid alive but both heartbeats stale: the process is wedged (hung socket).
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 100,
                      last_liveness_at=NOW - 60, last_progress_at=NOW - 300)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "stalled"
    assert v.reason == "no_heartbeat"
    assert v.event["type"] == events.RUN_STALLED


def test_slow_but_healthy_does_not_flip():
    # pid alive, side-thread liveness fresh, only progress stale, under timeout.
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 100,
                      last_liveness_at=NOW - 5, last_progress_at=NOW - 200)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "slow"
    # the load-bearing assertion: a slow-but-alive run is NEVER flipped terminal
    assert v.event is None or v.event["type"] not in events.TERMINAL_TYPES


def test_absolute_timeout_flips_to_failed():
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 5000,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "failed"
    assert v.reason == "timeout"


def test_dead_pid_takes_precedence_over_timeout():
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 5000,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.reason == "process_died"


def test_healthy_running_is_noop():
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 10,
                      last_liveness_at=NOW - 2, last_progress_at=NOW - 2)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_alive)
    assert v.action == "none"
    assert v.event is None


def test_already_terminal_is_noop():
    run = RunLiveness(run_id="agents:a", status="completed", pid=4242, started_at=NOW - 10,
                      last_liveness_at=None, last_progress_at=None, is_terminal=True)
    v = reconciler.classify(run, now=NOW, cfg=CFG, pid_alive=_dead)
    assert v.action == "none"


# --------------------------------------------------------------------------- #
# emit + idempotency against the real spine
# --------------------------------------------------------------------------- #

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
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_spine_local()
    yield
    _reset_spine_local()


def test_reconcile_and_emit_is_idempotent(spine):
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 5,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    verdicts1 = reconciler.reconcile_and_emit([run], now=NOW, cfg=CFG, pid_alive=_dead)
    verdicts2 = reconciler.reconcile_and_emit([run], now=NOW, cfg=CFG, pid_alive=_dead)
    assert verdicts1[0].action == "failed"
    assert verdicts2[0].action == "failed"
    # Two reconcile passes must write exactly one terminal event (dedupe).
    failed = [e for e in spine_db.tail_since(0) if e["type"] == events.RUN_FAILED]
    assert len(failed) == 1
    assert failed[0]["payload"]["reason"] == "process_died"


def test_reconcile_and_emit_skips_healthy_runs(spine):
    run = RunLiveness(run_id="agents:a", status="working", pid=4242, started_at=NOW - 5,
                      last_liveness_at=NOW - 1, last_progress_at=NOW - 1)
    reconciler.reconcile_and_emit([run], now=NOW, cfg=CFG, pid_alive=_alive)
    assert [e for e in spine_db.tail_since(0) if e["type"] in events.TERMINAL_TYPES] == []
