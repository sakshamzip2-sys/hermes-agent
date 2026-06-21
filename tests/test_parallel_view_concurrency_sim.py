"""Phase 5 concurrency simulation: the parallel view is truthful under load.

Spins up 12 real worker processes concurrently as oc_agents sessions, drives a
mixed outcome (some complete, some are SIGKILLed, some keep running), then runs
the watchdog reconciler from MULTIPLE threads at once (race hunt) and folds the
spine into RunViews. Every run must show its true state, and each killed run must
have exactly ONE run.failed event despite concurrent reconcilers (idempotency
under contention). Real processes, real threads, no mocks.
"""

from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from plugins.oc_agents import db as agents_db
from plugins.oc_runs import agents_adapter, drainer
from plugins.oc_runs import db as spine_db
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


def test_truthful_under_concurrent_load(env):
    procs = []
    plan = ["complete"] * 4 + ["kill"] * 4 + ["run"] * 4
    sessions = {}  # sid -> kind

    def launch(kind):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        procs.append(proc)
        sid = agents_db.new_session_id()
        agents_db.create_session(session_id=sid, prompt=f"{kind} task", name=kind)
        agents_db.set_pid(sid, proc.pid)
        agents_db.mark_working(sid)
        return kind, sid, proc

    try:
        # Launch all 12 concurrently.
        with ThreadPoolExecutor(max_workers=12) as ex:
            launched = list(ex.map(launch, plan))

        # Drive outcomes.
        for kind, sid, proc in launched:
            sessions[sid] = kind
            if kind == "complete":
                agents_db.finish_session(sid, agents_db.STATE_COMPLETED, result="ok")
                proc.kill(); proc.wait(timeout=10)
            elif kind == "kill":
                proc.kill(); proc.wait(timeout=10)
            # "run": leave the process alive.

        drainer.drain(agents_db.connect)

        # Race hunt: reconcile from 3 threads simultaneously.
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda _: agents_adapter.reconcile_agents(now=time.time()), range(3)))
        drainer.drain(agents_db.connect)

        views = {v["run_id"]: v for v in projection.build_view_from_spine()}
        all_events = spine_db.tail_since(0)

        for sid, kind in sessions.items():
            rid = f"agents:{sid}"
            assert rid in views, f"{rid} missing from the view"
            state = views[rid]["state"]
            if kind == "complete":
                assert state == "completed", (rid, state)
            elif kind == "kill":
                assert state == "failed", (rid, state)
                # Idempotency under concurrent reconcile: exactly one terminal.
                fails = [e for e in all_events
                         if e["run_id"] == rid and e["type"] == "run.failed"]
                assert len(fails) == 1, (rid, len(fails))
            else:
                assert state == "running", (rid, state)
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
