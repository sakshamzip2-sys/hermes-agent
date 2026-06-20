"""Intent-then-execute recovery (Feature C).

A bare attempt counter dedupes the DECISION to retry but not the SIDE EFFECT
(the spawn), so a crash between persisting the decision and launching the
process either abandons the task or double-spawns. Intent-then-execute fixes
this:

  1. In one BEGIN IMMEDIATE transaction: claim the recovery via a UNIQUE
     recovery_claims row (so concurrent reconcilers cannot both recover the same
     failure), reserve a ledger slot (caps.reserve_slot_locked), and write a
     spawn_intent row in state 'pending'. Only the claim winner proceeds.
  2. OUTSIDE the transaction: perform the side effect (the spawn). On success,
     flip the intent to 'launched' with the child id.
  3. On every tick, reconcile_intents re-executes any intent stuck 'pending'
     with no child (a crash between step 1 and step 2). The reservation is
     already held and the claim already dedupes, so re-execution is safe.

The spawn is INJECTED (spawn_fn) so the policy is testable without real
subprocesses and so retry always routes through the capped reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from . import caps
from . import db as odb


@dataclass
class RecoveryResult:
    action: str  # retried | already_claimed | exhausted | refused
    attempt_no: int = 0
    intent_id: Optional[str] = None
    child_id: Optional[str] = None
    detail: str = ""


def _execute_intent(conn, intent_id: str, spawn_fn: Callable, *, attempt_no: int) -> RecoveryResult:
    try:
        child_id = spawn_fn(attempt_no=attempt_no, intent_id=intent_id)
    except Exception as exc:  # noqa: BLE001 - leave intent pending for a retry tick
        return RecoveryResult("retried", attempt_no=attempt_no, intent_id=intent_id,
                              detail=f"spawn deferred: {exc}")
    conn.execute(
        "UPDATE spawn_intents SET state='launched', child_id=?, updated_at=? WHERE id=?",
        (str(child_id), odb.now(), intent_id),
    )
    return RecoveryResult("retried", attempt_no=attempt_no, intent_id=intent_id, child_id=str(child_id))


def attempt_recovery(
    conn,
    *,
    run_tree_id: str,
    task_id: str,
    failure_seq: int,
    spawn_fn: Callable,
    max_attempts: int = 3,
    est_usd: float = 0.0,
    depth: int = 1,
    parent_node: str = "",
) -> RecoveryResult:
    """Recover one failed task. Returns retried / already_claimed / exhausted /
    refused. Idempotent: concurrent callers on the same failure_seq collapse to
    one recovery via the UNIQUE claim."""
    caps.ensure_tree(conn, run_tree_id)
    conn.execute("BEGIN IMMEDIATE")
    intent_id: Optional[str] = None
    attempt_no = 0
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO recovery_claims
               (run_tree_id, task_id, failure_seq, attempt_no, claimed_at)
               VALUES (?,?,?,?,?)""",
            (run_tree_id, task_id, int(failure_seq), 0, odb.now()),
        )
        if cur.rowcount == 0:
            conn.execute("ROLLBACK")
            return RecoveryResult("already_claimed")

        n = conn.execute(
            "SELECT COUNT(*) c FROM spawn_intents WHERE run_tree_id=? AND task_id=?",
            (run_tree_id, task_id),
        ).fetchone()["c"]
        attempt_no = int(n) + 1

        if attempt_no > max_attempts:
            conn.execute("ROLLBACK")
            odb.record_decision(conn, run_tree_id, "recovery",
                                {"action": "exhausted", "task_id": task_id, "attempt_no": attempt_no})
            return RecoveryResult("exhausted", attempt_no=attempt_no)

        d = caps.reserve_slot_locked(conn, run_tree_id, parent_node=parent_node, depth=depth, est_usd=est_usd)
        if not d.ok:
            conn.execute("ROLLBACK")
            odb.record_decision(conn, run_tree_id, "recovery",
                                {"action": "refused", "cap": d.refused_cap, "task_id": task_id})
            return RecoveryResult("refused", attempt_no=attempt_no, detail=d.refused_cap)

        intent_id = odb.new_id()
        conn.execute(
            """INSERT INTO spawn_intents
               (id, run_tree_id, task_id, failure_seq, reservation_id, attempt_no, state, created_at, updated_at)
               VALUES (?,?,?,?,?,?, 'pending', ?, ?)""",
            (intent_id, run_tree_id, task_id, int(failure_seq), d.reservation_id, attempt_no,
             odb.now(), odb.now()),
        )
        odb.record_decision(conn, run_tree_id, "recovery",
                            {"action": "retry", "task_id": task_id, "attempt_no": attempt_no,
                             "intent_id": intent_id})
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    # Side effect outside the transaction (crash here leaves the intent pending,
    # to be re-executed idempotently by reconcile_intents).
    return _execute_intent(conn, intent_id, spawn_fn, attempt_no=attempt_no)


def reconcile_intents(conn, spawn_fn: Callable) -> List[RecoveryResult]:
    """Re-execute intents stuck 'pending' with no child (a crash between intent
    creation and the spawn). Safe: the reservation is already held and the claim
    already dedupes, so this never double-reserves or double-claims."""
    rows = conn.execute(
        "SELECT id, attempt_no FROM spawn_intents WHERE state='pending' AND child_id IS NULL"
    ).fetchall()
    return [_execute_intent(conn, r["id"], spawn_fn, attempt_no=int(r["attempt_no"])) for r in rows]


def active_reservation_count(conn, run_tree_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM slot_reservations WHERE run_tree_id=? AND status='reserved'",
        (run_tree_id,),
    ).fetchone()
    return int(row["c"]) if row else 0
