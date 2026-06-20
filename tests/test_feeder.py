"""Read-triggered feeder: a live oc_agents session surfaces on the spine.

Proves the missing live link end to end: an oc_agents lifecycle (which writes to
its own run_outbox) is pulled into the spine by feed() WITHOUT a background
daemon, and a dead worker is reconciled to failed on the same read. This is what
makes the live SSE cockpit show real data. Real SQLite, real subprocess, no mocks.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from plugins.oc_agents import db as agents_db
from plugins.oc_runs import db as spine_db
from plugins.oc_runs import feeder
from plugins.parallel_view import projection


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


def test_feed_drains_agent_lifecycle_to_spine(env):
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="do a thing", name="demo")
    agents_db.mark_working(sid)
    # Before feed: the spine is empty (nothing drained yet).
    assert spine_db.tail_since(0) == []

    counts = feeder.feed()
    assert counts["agents"] >= 2  # created + status drained

    views = {v["run_id"]: v for v in projection.build_view_from_spine()}
    assert f"agents:{sid}" in views
    assert views[f"agents:{sid}"]["state"] == "running"


def test_feed_reconciles_dead_worker_to_failed(env):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        sid = agents_db.new_session_id()
        agents_db.create_session(session_id=sid, prompt="long task")
        agents_db.set_pid(sid, proc.pid)
        agents_db.mark_working(sid)
        feeder.feed()  # drains created/status; worker still alive
        run_id = f"agents:{sid}"
        assert projection.build_view_from_spine()  # has the run

        proc.kill(); proc.wait(timeout=10)
        feeder.feed()  # read-triggered reconcile flips the dead worker

        views = {v["run_id"]: v for v in projection.build_view_from_spine()}
        assert views[run_id]["state"] == "failed"
        # idempotent: a second feed does not add a duplicate terminal
        feeder.feed()
        fails = [e for e in spine_db.tail_since(0)
                 if e["run_id"] == run_id and e["type"] == "run.failed"]
        assert len(fails) == 1
    finally:
        if proc.poll() is None:
            proc.kill()


def test_feed_is_safe_when_nothing_to_do(env):
    assert feeder.feed()["agents"] == 0
    assert spine_db.tail_since(0) == []
