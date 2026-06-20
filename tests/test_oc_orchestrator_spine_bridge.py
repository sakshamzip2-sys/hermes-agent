"""End-to-end: spine failure -> orchestrator recovery (Feature B + C loop).

Proves the orchestrator takes care of failures surfaced on the spine: a
run.failed event drives exactly one idempotent retry (re-scanning the spine does
not re-recover), and recovery routes through the cap ledger.

Stdlib + pytest only, real SQLite, no mocks (spawn injected).
"""

from __future__ import annotations

import pytest

from plugins.oc_orchestrator import db as odb
from plugins.oc_orchestrator import spine_bridge
from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events, reconciler
from plugins.oc_runs.reconciler import ReconcileConfig, RunLiveness


def _reset(local):
    for attr in ("conn", "path"):
        if hasattr(local, attr):
            try:
                if attr == "conn" and local.conn is not None:
                    local.conn.close()
            except Exception:
                pass
            delattr(local, attr)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    monkeypatch.setenv("HERMES_OC_ORCHESTRATOR_DB", str(tmp_path / "oc_orchestrator.db"))
    _reset(spine_db._local)
    _reset(odb._local)
    yield
    _reset(spine_db._local)
    _reset(odb._local)


class FakeSpawner:
    def __init__(self):
        self.calls = []

    def __call__(self, *, attempt_no, intent_id):
        self.calls.append((attempt_no, intent_id))
        return f"retry-child-{attempt_no}"


def test_spine_failure_drives_one_recovery(env):
    # The reconciler writes a truthful run.failed onto the spine (dead pid).
    run = RunLiveness(run_id="agents:abc", status="working", pid=999999,
                      started_at=1000.0, last_liveness_at=1000.0, last_progress_at=1000.0)
    reconciler.reconcile_and_emit([run], now=2000.0,
                                  cfg=ReconcileConfig(absolute_timeout=10_000),
                                  pid_alive=lambda *_: False)
    assert any(e["type"] == events.RUN_FAILED for e in spine_db.tail_since(0))

    sp = FakeSpawner()
    with odb.connect() as conn:
        results = spine_bridge.recover_failures(conn, spawn_fn=sp)
        assert len(results) == 1
        run_id, result = results[0]
        assert run_id == "agents:abc"
        assert result.action == "retried"
        assert result.child_id == "retry-child-1"
        assert len(sp.calls) == 1

        # Re-scanning the spine must NOT recover the same failure again.
        results2 = spine_bridge.recover_failures(conn, spawn_fn=sp)
        assert results2[0][1].action == "already_claimed"
        assert len(sp.calls) == 1


def test_no_failures_is_noop(env):
    spine_db.append_event(events.build_event("agents:ok", events.RUN_COMPLETED, source=events.SOURCE_AGENTS))
    sp = FakeSpawner()
    with odb.connect() as conn:
        assert spine_bridge.recover_failures(conn, spawn_fn=sp) == []
        assert len(sp.calls) == 0
