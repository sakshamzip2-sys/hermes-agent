#!/usr/bin/env python3
"""Runnable proof for the Parallel Agents truth-under-failure substrate.

This demonstrates, end to end and with a REAL process, that the run-event spine
reflects an agent's lifecycle live and reports failure truthfully:

  1. an agent session emits its lifecycle (created -> running -> progress) into
     the durable spine via the transactional outbox + drainer,
  2. a real child process stands in for a worker and is shown running,
  3. the process is SIGKILLed, and the three-signal reconciler flips it to a
     truthful run.failed on the spine, never a stale "running",
  4. re-running the reconciler is idempotent (no duplicate terminal event).

It is isolated to a temp directory (it does NOT touch ~/.hermes), so it is safe
and re-runnable. Run it with the project venv:

    .venv/bin/python scripts/demo_parallel_view.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Make the repo's top-level packages importable when run as a script.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _hr(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oc-parallel-demo-"))
    os.environ["HERMES_OC_AGENTS_DB"] = str(tmp / "oc_agents.db")
    os.environ["HERMES_OC_RUNS_DB"] = str(tmp / "oc_runs.db")

    from plugins.oc_agents import db as agents_db
    from plugins.oc_runs import agents_adapter, drainer
    from plugins.oc_runs import db as spine_db

    def show_spine(run_id: str) -> None:
        for e in spine_db.tail_since(0):
            if e["run_id"] == run_id:
                extra = e["payload"].get("reason") or e["payload"].get("status") or ""
                print(f"  seq={e['seq']:>3}  {e['type']:<14} {extra}")

    print(f"isolated run-state in: {tmp}")

    # 1. A healthy lifecycle flows into the spine.
    _hr("1. agent lifecycle -> spine (via outbox + drainer)")
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="summarize the repo", name="demo-agent")
    agents_db.mark_working(sid)
    agents_db.add_event(sid, "running tool: read_file", kind="tool")
    drained = drainer.drain(agents_db.connect)
    run_id = f"agents:{sid}"
    print(f"drained {drained} outbox events into the spine; spine now shows {run_id}:")
    show_spine(run_id)

    # 2. A real process stands in for the worker and is shown running.
    _hr("2. real worker process running")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    agents_db.set_pid(sid, proc.pid)
    print(f"worker pid={proc.pid} alive; reconciler verdict while alive:")
    v_alive = next(x for x in agents_adapter.reconcile_agents(now=time.time()) if x.run_id == run_id)
    print(f"  action={v_alive.action!r} (not failed: the live worker is left running)")

    # 3. Kill the worker. The reconciler must tell the truth.
    _hr("3. SIGKILL the worker -> truthful run.failed (guardrail 8)")
    proc.kill()
    proc.wait(timeout=10)
    print(f"sent SIGKILL to pid={proc.pid}; running proactive reconcile...")
    v = next(x for x in agents_adapter.reconcile_agents(now=time.time()) if x.run_id == run_id)
    print(f"  reconciler verdict: action={v.action!r} reason={v.reason!r}")
    show_spine(run_id)

    # 4. Idempotency.
    _hr("4. idempotent reconcile (no duplicate terminal)")
    agents_adapter.reconcile_agents(now=time.time())
    failed = [e for e in spine_db.tail_since(0)
              if e["run_id"] == run_id and e["type"] == "run.failed"]
    print(f"  run.failed events after two reconciles: {len(failed)} (expected 1)")

    ok = v.action == "failed" and v.reason == "process_died" and len(failed) == 1
    _hr("RESULT")
    print("PASS: the parallel view tells the truth under failure." if ok
          else "FAIL: truth-under-failure not demonstrated.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
