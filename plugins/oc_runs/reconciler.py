"""Truth-under-failure reconciler (guardrail 8): a run that crashed, hung, or
timed out must show as failed or stalled, never a stale 'working'.

The decision is a PURE function, ``classify``, that couples three independent
signals so no single failure mode lies:

  - pid liveness, validated by pid PLUS process start-time (a reused pid with a
    different start-time reads as dead, killing the os.kill(pid,0) false positive
    the recon flagged in oc_agents.reconcile_liveness),
  - a progress-coupled heartbeat (work is advancing) and a side-thread liveness
    heartbeat (the process is alive but maybe wedged), treated separately,
  - an absolute wall-clock timeout.

Precedence: a dead pid is failure regardless of anything else. A live process
with both heartbeats stale is wedged -> stalled. A live process with only the
progress beat stale is slow-but-healthy and is NEVER flipped (the case a single
heartbeat gets wrong in both directions). Fresh runs get a startup grace so a
worker that has not beaten yet is not falsely stalled.

``reconcile_and_emit`` applies ``classify`` over a batch and writes terminal (or
slow-annotation) events into the spine, idempotently (dedupe per run + action).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import db as spine_db
from . import events

TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped", "stalled"})


@dataclass
class ReconcileConfig:
    # No side-thread liveness beat within this window => the process may be wedged.
    liveness_window: float = 45.0
    # No progress-coupled beat within this window => progress has stalled.
    progress_window: float = 180.0
    # Hard wall-clock cap per run regardless of heartbeats.
    absolute_timeout: float = 1800.0


@dataclass
class RunLiveness:
    run_id: str
    status: str = "working"
    pid: Optional[int] = None
    pid_start_time: Optional[float] = None
    started_at: Optional[float] = None
    last_liveness_at: Optional[float] = None
    last_progress_at: Optional[float] = None
    is_terminal: bool = False


@dataclass
class Verdict:
    run_id: str
    action: str  # none | failed | stalled | slow
    reason: str = ""
    event: Optional[Dict[str, Any]] = None


def default_pid_alive(pid: Optional[int], start_time: Optional[float] = None) -> bool:
    """Real liveness probe. Uses psutil for pid + start-time validation when
    available (so a reused pid reads as dead); falls back to os.kill liveness."""
    if not pid:
        return False
    try:
        import psutil  # type: ignore

        try:
            p = psutil.Process(int(pid))
        except psutil.NoSuchProcess:
            return False
        if start_time is not None:
            try:
                if abs(p.create_time() - float(start_time)) > 1.0:
                    return False  # pid reused by a different process
            except Exception:
                pass
        try:
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return True
    except Exception:
        # psutil unavailable: liveness-only (no start-time validation).
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # exists but not ours
        except Exception:
            return False


def classify(
    run: RunLiveness,
    *,
    now: float,
    cfg: ReconcileConfig,
    pid_alive: Callable[[Optional[int], Optional[float]], bool] = default_pid_alive,
) -> Verdict:
    if run.is_terminal or run.status in TERMINAL_STATUSES:
        return Verdict(run.run_id, "none")

    # 1. Dead pid is failure, highest precedence (beats timeout, beats heartbeat).
    if run.pid and not pid_alive(run.pid, run.pid_start_time):
        return Verdict(
            run.run_id, "failed", "process_died",
            events.build_event(run.run_id, events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                               payload={"reason": "process_died"}, dedupe_key="reconciler:failed"),
        )

    # 2. Absolute wall-clock timeout.
    if run.started_at is not None and (now - run.started_at) > cfg.absolute_timeout:
        return Verdict(
            run.run_id, "failed", "timeout",
            events.build_event(run.run_id, events.RUN_FAILED, source=events.SOURCE_RECONCILER,
                               payload={"reason": "timeout"}, dedupe_key="reconciler:failed"),
        )

    # 3. Startup grace: too young to judge heartbeats yet.
    if run.started_at is not None and (now - run.started_at) < cfg.liveness_window:
        return Verdict(run.run_id, "none")

    # 4. Heartbeat staleness.
    liveness_stale = run.last_liveness_at is None or (now - run.last_liveness_at) > cfg.liveness_window
    progress_stale = run.last_progress_at is None or (now - run.last_progress_at) > cfg.progress_window

    if liveness_stale and progress_stale:
        return Verdict(
            run.run_id, "stalled", "no_heartbeat",
            events.build_event(run.run_id, events.RUN_STALLED, source=events.SOURCE_RECONCILER,
                               payload={"reason": "no_heartbeat"}, dedupe_key="reconciler:stalled"),
        )

    if progress_stale:
        # Liveness fresh, progress stale: slow but healthy. Surface honestly,
        # never flip terminal.
        return Verdict(
            run.run_id, "slow", "no_recent_progress",
            events.build_event(run.run_id, events.RUN_STATUS, source=events.SOURCE_RECONCILER,
                               payload={"display": "running", "annotation": "slow",
                                        "reason": "no_recent_progress"},
                               dedupe_key="reconciler:slow"),
        )

    return Verdict(run.run_id, "none")


def reconcile_and_emit(
    runs: List[RunLiveness],
    *,
    now: Optional[float] = None,
    cfg: Optional[ReconcileConfig] = None,
    pid_alive: Callable[[Optional[int], Optional[float]], bool] = default_pid_alive,
) -> List[Verdict]:
    """Classify a batch and write any resulting events into the spine. Idempotent:
    terminal/slow events carry a per-run dedupe key so repeated passes collapse."""
    now = now if now is not None else time.time()
    cfg = cfg or ReconcileConfig()
    verdicts: List[Verdict] = []
    for run in runs:
        v = classify(run, now=now, cfg=cfg, pid_alive=pid_alive)
        if v.event is not None:
            spine_db.append_event(v.event)
        verdicts.append(v)
    return verdicts
