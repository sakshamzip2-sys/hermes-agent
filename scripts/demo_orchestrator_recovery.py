#!/usr/bin/env python3
"""Runnable proof that the orchestrator takes care of failures.

End to end, with a REAL process:
  1. a worker is dispatched and shown running on the spine,
  2. the worker is SIGKILLed; the reconciler writes a truthful run.failed,
  3. the orchestrator reads that failure off the spine and issues exactly one
     idempotent retry through the capped reservation ledger,
  4. re-scanning does not double-recover (idempotent).

Isolated to a temp directory (does NOT touch ~/.hermes); re-runnable. Run with:

    .venv/bin/python scripts/demo_orchestrator_recovery.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _hr(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="oc-orch-demo-"))
    os.environ["HERMES_OC_AGENTS_DB"] = str(tmp / "oc_agents.db")
    os.environ["HERMES_OC_RUNS_DB"] = str(tmp / "oc_runs.db")
    os.environ["HERMES_OC_ORCHESTRATOR_DB"] = str(tmp / "oc_orchestrator.db")

    from plugins.oc_agents import db as agents_db
    from plugins.oc_orchestrator import caps
    from plugins.oc_orchestrator import db as odb
    from plugins.oc_orchestrator import spine_bridge
    from plugins.oc_runs import agents_adapter
    from plugins.oc_runs import db as spine_db

    print(f"isolated run-state in: {tmp}")

    # A real worker process is dispatched and shown running.
    _hr("1. real worker running")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="flaky task", name="worker")
    agents_db.set_pid(sid, proc.pid)
    agents_db.mark_working(sid)
    run_id = f"agents:{sid}"
    print(f"worker pid={proc.pid} run_id={run_id}")

    # SIGKILL -> reconciler writes truthful run.failed.
    _hr("2. SIGKILL -> reconciler writes truthful run.failed")
    proc.kill()
    proc.wait(timeout=10)
    agents_adapter.reconcile_agents(now=time.time())
    failed = [e for e in spine_db.tail_since(0) if e["run_id"] == run_id and e["type"] == "run.failed"]
    print(f"spine run.failed events for {run_id}: {len(failed)} (reason={failed[0]['payload']['reason']})")

    # Orchestrator recovers it (here the retry spawn is simulated as completing).
    _hr("3. orchestrator recovers the failure (capped, idempotent)")

    def retry_spawn(*, attempt_no, intent_id):
        # In production this re-dispatches a fresh worker via supervisor.dispatch
        # through spawn_guarded. Here we simulate a successful retry child.
        print(f"  orchestrator re-dispatching: attempt {attempt_no} (intent {intent_id})")
        return f"retry-child-{attempt_no}"

    with odb.connect() as conn:
        # Bound the tree so the demo also shows caps are active.
        caps.ensure_tree(conn, run_id, caps_overrides={"max_concurrent": 4}, budget_usd=5.0)
        results = spine_bridge.recover_failures(conn, spawn_fn=retry_spawn)
        action = results[0][1].action if results else "none"
        child = results[0][1].child_id if results else None
        print(f"  recovery action={action!r} child={child!r}")

        # Idempotent re-scan.
        results2 = spine_bridge.recover_failures(conn, spawn_fn=retry_spawn)
        action2 = results2[0][1].action if results2 else "none"
        print(f"  re-scan action={action2!r} (expected already_claimed)")

    ok = action == "retried" and child == "retry-child-1" and action2 == "already_claimed" and len(failed) == 1
    _hr("RESULT")
    print("PASS: the orchestrator detects a real failure and recovers it once, under caps."
          if ok else "FAIL: orchestrator recovery not demonstrated.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
