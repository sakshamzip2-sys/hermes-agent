"""Connect the run-event spine to orchestrator recovery.

The reconciler writes a truthful ``run.failed`` into the spine when a worker
dies, hangs, or times out. This bridge reads those failures and drives an
idempotent retry through ``recovery.attempt_recovery``. Together they close the
loop: a failure becomes visible and is then recovered, under the cap ledger.

Idempotency rests on the spine seq: each ``run.failed`` event has a unique,
durable ``seq`` used as the recovery ``failure_seq``, so re-scanning the spine
(or two concurrent supervisors scanning) recovers each failure exactly once via
the UNIQUE recovery_claims row.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from plugins.oc_runs import db as spine_db

from . import recovery


def find_failures(since_seq: int = 0) -> List[Tuple[str, int]]:
    """Return (run_id, seq) for every run.failed event with seq > since_seq."""
    return [
        (e["run_id"], e["seq"])
        for e in spine_db.tail_since(since_seq)
        if e["type"] == "run.failed"
    ]


def recover_failures(
    conn,
    *,
    spawn_fn: Callable,
    run_tree_of: Optional[Callable[[str], str]] = None,
    max_attempts: int = 3,
    since_seq: int = 0,
    est_usd: float = 0.0,
) -> List[Tuple[str, recovery.RecoveryResult]]:
    """For each unrecovered failure on the spine, attempt an idempotent retry.

    ``run_tree_of`` maps a run_id to its owning run-tree (for cap accounting);
    by default a run is its own tree. Returns (run_id, result) per failure.
    """
    out: List[Tuple[str, recovery.RecoveryResult]] = []
    for run_id, seq in find_failures(since_seq):
        tree = run_tree_of(run_id) if run_tree_of else run_id
        result = recovery.attempt_recovery(
            conn,
            run_tree_id=tree,
            task_id=run_id,
            failure_seq=seq,
            spawn_fn=spawn_fn,
            max_attempts=max_attempts,
            est_usd=est_usd,
        )
        out.append((run_id, result))
    return out
