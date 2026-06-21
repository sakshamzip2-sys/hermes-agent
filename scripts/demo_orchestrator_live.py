#!/usr/bin/env python3
"""Live orchestrator end-to-end: real worker fails -> orchestrator recovers it.

Uses the REAL gateway dispatch path (oc_agents.supervisor) and the real run-state
DBs, tying together the three layers the orchestrator owns:
  1. caps.spawn_guarded reserves a slot in the atomic ledger (runaway-fanout-proof),
  2. a REAL detached worker process is dispatched and then SIGKILLed,
  3. the read-triggered feeder writes a truthful run.failed onto the spine, and
  4. spine_bridge.recover_failures issues ONE idempotent retry via a real
     re-dispatch, under the same cap ledger.

This complements demo_orchestrator_recovery.py (isolated DBs) with a live run on
the real gateway state. Run with the gateway env present:
    .venv/bin/python scripts/demo_orchestrator_live.py
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pin a unique run-tree so this demo never collides with real fleet state.
TREE = f"live-orch-demo-{int(time.time())}"


def _hr(t):
    print(f"\n=== {t} ===")


def main() -> int:
    from plugins.oc_agents import db as agents_db
    from plugins.oc_agents import supervisor
    from plugins.oc_orchestrator import caps, spine_bridge
    from plugins.oc_orchestrator import db as odb
    from plugins.oc_runs import db as spine_db
    from plugins.oc_runs import feeder

    _hr("1. cap ledger: reserve a slot before spawning (spawn_guarded)")
    with odb.connect() as conn:
        caps.ensure_tree(conn, TREE, caps_overrides={"max_concurrent": 4}, budget_usd=5.0)
        d = caps.spawn_guarded(conn, TREE, depth=1, est_usd=0.01)
    print(f"reserved slot ok={d.ok} reservation={d.reservation_id}")

    _hr("2. dispatch a REAL detached worker via the gateway supervisor")
    sid = supervisor.dispatch(
        "Reply with the single word ALIVE.",
        name=f"orch-live-{TREE[-6:]}", model="claude-haiku-4-5",
    )
    row = agents_db.get_session(sid)
    pid = row and row.get("pid")
    run_id = f"agents:{sid}"
    print(f"dispatched session={sid} pid={pid} run_id={run_id}")
    time.sleep(1.5)

    _hr("3. SIGKILL the real worker")
    if pid:
        try:
            os.kill(int(pid), signal.SIGKILL)
            print(f"killed pid={pid}")
        except ProcessLookupError:
            print(f"pid {pid} already gone")
    time.sleep(1.0)

    _hr("4. read-triggered feeder -> truthful run.failed on the spine")
    feeder.feed()
    fails = [e for e in spine_db.tail_since(0)
             if e["run_id"] == run_id and e["type"] == "run.failed"]
    print(f"run.failed events for {run_id}: {len(fails)} "
          f"(reason={fails[0]['payload'].get('reason') if fails else None})")

    _hr("5. orchestrator recovers it: ONE idempotent retry via REAL re-dispatch, capped")
    retried = {}

    def respawn(*, attempt_no, intent_id):
        new_sid = supervisor.dispatch(
            "Reply with the single word RETRIED.",
            name=f"orch-retry-{TREE[-6:]}-a{attempt_no}", model="claude-haiku-4-5",
        )
        retried["sid"] = new_sid
        print(f"  orchestrator re-dispatched attempt {attempt_no}: new session={new_sid}")
        return new_sid

    with odb.connect() as conn:
        results = spine_bridge.recover_failures(
            conn, spawn_fn=respawn, run_tree_of=lambda _r: TREE, max_attempts=3)
        # idempotent re-scan
        results2 = spine_bridge.recover_failures(
            conn, spawn_fn=respawn, run_tree_of=lambda _r: TREE, max_attempts=3)

    action = next((r.action for rid, r in results if rid == run_id), "none")
    action2 = next((r.action for rid, r in results2 if rid == run_id), "none")
    print(f"  recovery action={action!r}  re-scan action={action2!r} (expected retried, already_claimed)")

    _hr("RESULT")
    ok = (d.ok and len(fails) == 1 and action == "retried"
          and action2 == "already_claimed" and retried.get("sid"))
    print("PASS: live orchestrator detected a real worker failure and recovered it once, under caps"
          if ok else "FAIL: live orchestrator recovery not demonstrated")
    # Clean up the retry worker so the demo leaves no runaway process.
    try:
        if retried.get("sid"):
            supervisor.stop(retried["sid"])
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
