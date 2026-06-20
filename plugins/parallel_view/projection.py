"""Pure-fold projection: oc_runs spine events -> normalized RunView list.

A RunView is the cockpit-facing shape for one run. The fold processes a run's
events in seq order; the last terminal-setting event by seq wins, so a real
engine ``run.completed`` (higher seq) correctly supersedes a reconciler
``run.stalled`` written while the worker was merely slow. State is normalized
through one table; anything unmapped becomes ``unknown``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# The single normalized state vocabulary.
STATES = ("pending", "running", "needs_input", "completed", "failed", "stopped", "stalled", "unknown")
TERMINAL = frozenset({"completed", "failed", "stopped", "stalled"})

_NATIVE_TO_NORMAL = {
    "pending": "pending",
    "working": "running",
    "running": "running",
    "needs_input": "needs_input",
    "completed": "completed",
    "failed": "failed",
    "stopped": "stopped",
    "stalled": "stalled",
}

_TYPE_TO_STATE = {
    "run.created": "pending",
    "run.completed": "completed",
    "run.failed": "failed",
    "run.stalled": "stalled",
}


def normalize_state(native: Optional[str]) -> str:
    """Map a native status to the normalized vocabulary; unmapped -> unknown."""
    return _NATIVE_TO_NORMAL.get((native or "").lower(), "unknown")


def fold_run(run_id: str, events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    view: Dict[str, Any] = {
        "run_id": run_id,
        "state": "pending",
        "parent_run_id": None,
        "source": None,
        "agent_id": None,
        "team_id": None,
        "title": "",
        "slow": False,
        "reason": None,
        "last_seq": 0,
    }
    for e in sorted(events, key=lambda x: x["seq"]):
        view["last_seq"] = e["seq"]
        if e.get("parent_run_id"):
            view["parent_run_id"] = e["parent_run_id"]
        if e.get("source"):
            view["source"] = e["source"]
        if e.get("agent_id"):
            view["agent_id"] = e["agent_id"]
        if e.get("team_id"):
            view["team_id"] = e["team_id"]

        t = e["type"]
        p = e.get("payload") or {}
        if t == "run.created":
            view["state"] = "pending"
            if p.get("name"):
                view["title"] = p["name"]
        elif t == "run.status":
            st = p.get("status")
            if st:
                view["state"] = normalize_state(st)
            view["slow"] = (p.get("annotation") == "slow")
        elif t in _TYPE_TO_STATE:
            view["state"] = _TYPE_TO_STATE[t]
            view["reason"] = p.get("reason") or p.get("status") or view["reason"]
            if t != "run.stalled":
                view["slow"] = False
        # run.progress / heartbeat / tool.* / team.message: keep state as-is.
    return view


def build_view(events: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group events by run_id and fold each into a RunView (sorted by last_seq)."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for e in events:
        groups.setdefault(e["run_id"], []).append(e)
    views = [fold_run(rid, evs) for rid, evs in groups.items()]
    views.sort(key=lambda v: v["last_seq"])
    return views


def build_view_from_spine(since_seq: int = 0) -> List[Dict[str, Any]]:
    """Convenience: fold the whole spine (or the tail past since_seq)."""
    from plugins.oc_runs import db as spine_db

    return build_view(spine_db.tail_since(since_seq, limit=1_000_000))
