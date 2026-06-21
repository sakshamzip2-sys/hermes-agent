"""The bounded, cache-safe team driver loop oc_teams lacks today.

oc_teams momentum depends on the model obeying a prose protocol; if it stops
polling, coordination stalls. The driver fixes that deterministically. ``tick``
is a PURE function over team state that returns ACTIONS; ``apply_actions``
executes them. The only cache-safe way to advance a running teammate is an
appended user turn via the existing bg_inbox queue (never a system-prompt or
toolset mutation mid-conversation), so a nudge is an inbox message, not a prompt
rebuild. Nudges are rate-limited; after the budget is spent the teammate is
escalated to terminate, never nudged forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass
class Teammate:
    name: str
    pid_alive: bool
    last_heartbeat_age: float  # seconds since last beat
    status: str = "active"     # active | idle | shutdown
    nudges_sent: int = 0


@dataclass
class Task:
    task_id: str
    status: str           # pending | in_progress | completed | failed | aborted
    depends_on: List[str] = field(default_factory=list)
    owner: str = ""


@dataclass
class DriverAction:
    kind: str    # nudge | terminate | block_dead_dep | dep_unblocked
    target: str
    reason: str = ""


@dataclass
class DriverConfig:
    idle_window: float = 60.0
    max_nudges: int = 3


def tick(teammates: List[Teammate], tasks: List[Task], *, cfg: Optional[DriverConfig] = None) -> List[DriverAction]:
    """Decide driver actions for one team tick. Pure and deterministic."""
    cfg = cfg or DriverConfig()
    actions: List[DriverAction] = []
    by_id: Dict[str, Task] = {t.task_id: t for t in tasks}

    # 1. Stalled teammates: pid alive but heartbeat stale. Nudge while under the
    #    budget, then escalate to terminate (a dead pid is the reconciler's job).
    for tm in teammates:
        if tm.status == "shutdown" or not tm.pid_alive:
            continue
        if tm.last_heartbeat_age > cfg.idle_window:
            if tm.nudges_sent < cfg.max_nudges:
                actions.append(DriverAction("nudge", tm.name, "idle"))
            else:
                actions.append(DriverAction("terminate", tm.name, "no_progress_after_nudges"))

    # 2. Dead-dependency: a pending task with a failed/aborted upstream can never
    #    become claimable; mark it blocked so it does not hang forever.
    for t in tasks:
        if t.status == "pending" and any(
            by_id.get(d) and by_id[d].status in ("failed", "aborted") for d in t.depends_on
        ):
            actions.append(DriverAction("block_dead_dep", t.task_id, "upstream_failed"))

    # 3. Newly claimable: a pending unowned task whose deps all completed. Surface
    #    dep.unblocked so the cockpit shows momentum and the lead can assign.
    for t in tasks:
        if (
            t.status == "pending"
            and not t.owner
            and t.depends_on
            and all(by_id.get(d) and by_id[d].status == "completed" for d in t.depends_on)
        ):
            actions.append(DriverAction("dep_unblocked", t.task_id, "deps_satisfied"))

    return actions


def apply_actions(
    actions: List[DriverAction],
    *,
    team_id: str,
    enqueue_nudge: Callable[[str, str], None],
    now: Optional[float] = None,
) -> int:
    """Execute driver actions: enqueue cache-safe inbox nudges and emit spine
    events for observability. Returns the number of actions applied. The atomic
    claim is never touched here; the driver only reads claim state and posts
    inbox/spine messages."""
    from plugins.oc_runs import db as spine_db
    from plugins.oc_runs import events as ev

    applied = 0
    for a in actions:
        if a.kind == "nudge":
            enqueue_nudge(a.target, "Driver: you appear idle. Continue your task or report a blocker.")
            spine_db.append_event(ev.build_event(
                f"teams:{team_id}:{a.target}", "orchestrator.nudge",
                source=ev.SOURCE_ORCHESTRATOR, team_id=team_id, payload={"reason": a.reason}))
        elif a.kind == "terminate":
            spine_db.append_event(ev.build_event(
                f"teams:{team_id}:{a.target}", "orchestrator.terminate",
                source=ev.SOURCE_ORCHESTRATOR, team_id=team_id, payload={"reason": a.reason}))
        elif a.kind == "dep_unblocked":
            spine_db.append_event(ev.build_event(
                f"teams:{team_id}", ev.DEP_UNBLOCKED,
                source=ev.SOURCE_ORCHESTRATOR, team_id=team_id, payload={"task_id": a.target}))
        elif a.kind == "block_dead_dep":
            spine_db.append_event(ev.build_event(
                f"teams:{team_id}", ev.DEP_BLOCKED,
                source=ev.SOURCE_ORCHESTRATOR, team_id=team_id,
                payload={"task_id": a.target, "reason": a.reason}))
        applied += 1
    return applied
