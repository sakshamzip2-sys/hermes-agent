"""GAP3: always-on reconciler loop wired into gateway startup.

The read-triggered feeder only fires on an SSE/snapshot read, so a crashed worker
could stay 'working' forever if nobody opens the cockpit. The gateway now runs a
bounded periodic reconcile loop. Verifies the loop exists and calls the feeder, is
registered at startup (gated by an opt-out env), and that one tick actually flips a
dead worker on the spine (real subprocess, real DBs).
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import sys

import pytest

from gateway.platforms.api_server import APIServerAdapter
from plugins.oc_agents import db as agents_db
from plugins.oc_runs import db as spine_db
from plugins.parallel_view import projection


def test_reconcile_loop_method_exists_and_uses_feeder():
    assert hasattr(APIServerAdapter, "_reconcile_runs_loop")
    src = inspect.getsource(APIServerAdapter._reconcile_runs_loop)
    assert "feeder" in src and "feed" in src


def test_reconcile_loop_registered_at_startup_with_optout():
    src = inspect.getsource(APIServerAdapter)
    assert "_reconcile_runs_loop()" in src
    assert "create_task(self._reconcile_runs_loop" in src
    assert "HERMES_RECONCILE_DISABLED" in src  # opt-out exists


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
    monkeypatch.setenv("HERMES_RECONCILE_INTERVAL", "0.2")  # fast tick for the test
    _reset(agents_db._local)
    _reset(spine_db._local)
    yield
    _reset(agents_db._local)
    _reset(spine_db._local)


def test_loop_tick_flips_dead_worker_on_spine(env):
    """Run the real loop briefly against a real killed worker and assert it flips
    to failed on the spine without any cockpit read."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sid = agents_db.new_session_id()
        agents_db.create_session(session_id=sid, prompt="long task")
        agents_db.set_pid(sid, proc.pid)
        agents_db.mark_working(sid)
        proc.kill(); proc.wait(timeout=10)

        async def run_one_tick():
            adapter = object.__new__(APIServerAdapter)  # no full init needed
            task = asyncio.create_task(adapter._reconcile_runs_loop())
            await asyncio.sleep(0.6)  # ~3 ticks at 0.2s
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run_one_tick())
        views = {v["run_id"]: v for v in projection.build_view_from_spine()}
        assert views[f"agents:{sid}"]["state"] == "failed"
    finally:
        if proc.poll() is None:
            proc.kill()
