"""Integration: oc_agents lifecycle and failure flow into the run-event spine.

Proves the real wiring end to end with REAL processes (no mocks of the kill
path): oc_agents lifecycle mutations enqueue outbox events that the drainer
moves into the spine; a worker whose process is actually SIGKILLed is flipped to
a truthful run.failed on the spine by the proactive reconciler (guardrail 8);
and dispatch pins the active HERMES_HOME into the worker env (the isolation
landmine fix). Stdlib + pytest only, no network, no LLM.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from plugins.oc_agents import db as agents_db
from plugins.oc_agents import supervisor
from plugins.oc_runs import agents_adapter, drainer
from plugins.oc_runs import db as spine_db


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
    monkeypatch.setenv("HERMES_OC_AGENTS_DB", str(tmp_path / "oc_agents.db"))
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset(agents_db._local)
    _reset(spine_db._local)
    yield
    _reset(agents_db._local)
    _reset(spine_db._local)


def _spine_types_for(run_id):
    return [e["type"] for e in spine_db.tail_since(0) if e["run_id"] == run_id]


def test_agents_lifecycle_drains_to_spine(env):
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="do x", name="demo")
    agents_db.mark_working(sid)
    agents_db.add_event(sid, "running tool: read_file", kind="tool")
    agents_db.finish_session(sid, agents_db.STATE_COMPLETED, result="done")

    drained = drainer.drain(agents_db.connect)
    assert drained >= 4

    types = _spine_types_for(f"agents:{sid}")
    assert "run.created" in types
    assert "run.status" in types
    assert "run.progress" in types
    assert "run.completed" in types


def test_failed_finish_emits_run_failed(env):
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="will fail")
    agents_db.mark_working(sid)
    agents_db.finish_session(sid, agents_db.STATE_FAILED, error="boom")
    drainer.drain(agents_db.connect)
    assert "run.failed" in _spine_types_for(f"agents:{sid}")


def test_real_worker_sigkill_flips_to_failed_on_spine(env):
    """The keystone truth-under-failure proof: a real child process is the
    worker; we SIGKILL it; the proactive reconciler must flip it to run.failed
    on the spine, never leave it 'working'."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sid = agents_db.new_session_id()
        agents_db.create_session(session_id=sid, prompt="long running task")
        agents_db.set_pid(sid, proc.pid)
        agents_db.mark_working(sid)
        run_id = f"agents:{sid}"

        # Sanity: before the kill, the worker is alive and NOT flagged failed.
        live_verdicts = agents_adapter.reconcile_agents(now=time.time())
        v_live = next(x for x in live_verdicts if x.run_id == run_id)
        assert v_live.action in ("none", "slow")
        assert "run.failed" not in _spine_types_for(run_id)

        # Kill the real worker process and reap it.
        proc.kill()
        proc.wait(timeout=10)

        # Proactive reconcile detects the dead pid and writes the truth.
        verdicts = agents_adapter.reconcile_agents(now=time.time())
        v = next(x for x in verdicts if x.run_id == run_id)
        assert v.action == "failed"
        assert v.reason == "process_died"

        failed = [e for e in spine_db.tail_since(0)
                  if e["run_id"] == run_id and e["type"] == "run.failed"]
        assert len(failed) == 1
        assert failed[0]["payload"]["reason"] == "process_died"

        # Idempotent: a second reconcile does not write a duplicate terminal.
        agents_adapter.reconcile_agents(now=time.time())
        failed2 = [e for e in spine_db.tail_since(0)
                   if e["run_id"] == run_id and e["type"] == "run.failed"]
        assert len(failed2) == 1
    finally:
        if proc.poll() is None:
            proc.kill()


def test_dispatch_pins_active_hermes_home(env, monkeypatch):
    """The isolation landmine fix: dispatch must pin the ACTIVE per-profile home
    (a ContextVar that does not touch os.environ) into the worker env, not the
    default home a detached process would otherwise inherit."""
    captured = {}

    class FakePopen:
        def __init__(self, *args, **kwargs):
            captured["env"] = kwargs.get("env")
            self.pid = 4242424

    import hermes_constants
    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: "/tmp/profile-A-home")
    monkeypatch.setattr(supervisor.subprocess, "Popen", FakePopen)

    supervisor.dispatch("a task", name="t")
    assert captured["env"] is not None
    assert captured["env"].get("HERMES_HOME") == "/tmp/profile-A-home"
