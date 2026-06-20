"""Adapter: reconcile live oc_agents sessions into the spine.

This is the proactive watchdog reconcile that runs ON TOP of oc_agents' own
read-triggered reconcile floor (it does not replace it). It maps each live
``bg_session`` to a ``RunLiveness`` and runs the three-signal reconciler, which
writes truthful ``run.failed`` / ``run.stalled`` / slow-annotation events into
the spine when a worker is dead, wedged, or slow. The oc_agents floor keeps
flipping the bg_session row itself, so the two planes agree without this module
writing back into oc_agents.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from plugins.oc_agents import db as agents_db

from . import reconciler
from .reconciler import ReconcileConfig, RunLiveness, Verdict, default_pid_alive


def _runliveness(row: dict) -> RunLiveness:
    status = row["status"]
    return RunLiveness(
        run_id=f"agents:{row['id']}",
        status=status,
        pid=row.get("pid"),
        # M1 uses pid-liveness only; pid+start-time reuse-guard is a later
        # hardening once oc_agents records the process start time.
        pid_start_time=None,
        started_at=row.get("started_at") or row.get("created_at"),
        last_progress_at=row.get("updated_at"),
        last_liveness_at=row.get("updated_at"),
        is_terminal=status in reconciler.TERMINAL_STATUSES,
    )


def reconcile_agents(
    *,
    now: Optional[float] = None,
    cfg: Optional[ReconcileConfig] = None,
    pid_alive: Callable[[Optional[int], Optional[float]], bool] = default_pid_alive,
) -> List[Verdict]:
    """Reconcile every live oc_agents session and emit spine events. Returns the
    verdicts (one per live session)."""
    rows = agents_db.list_sessions(include_done=False)
    runs = [_runliveness(r) for r in rows]
    return reconciler.reconcile_and_emit(runs, now=now, cfg=cfg, pid_alive=pid_alive)
