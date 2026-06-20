"""Deterministic Stage-1 routing: map a goal to a profile and a shape.

The orchestrator routes incoming work to the right specialized profile and decides
single-agent vs swarm. Per the design, Stage-1 is DETERMINISTIC (no model call):
cheap keyword triage with a safe fallback (route to a single profile), so routing
keeps working when the LLM brain is unavailable. The Stage-2 brain that decomposes
an ambiguous or parallelizable goal into a task DAG is the open item; this module
is its deterministic floor and its fallback.

route() returns the lead profile and shape; route_and_assign() turns that into a
real Kanban card assigned to the lead profile (via kanban_bridge), closing the
goal -> profile -> Kanban -> spine loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

# Domain keyword -> profile. Cheap, transparent, and easy to extend per profile.
_KEYWORDS = {
    "coder": ["code", "debug", "bug", "refactor", "unit test", "implement", "fix",
              "compile", "function", "script", "endpoint", "program", "stack trace"],
    "atlas": ["research", "investigate", "find out", "sources", "cite", "compare",
              "summarize", "look up", "briefing", "literature"],
    "sage": ["decide", "strategy", "strategic", "trade-off", "tradeoff",
             "pre-mortem", "pros and cons", "should we", "second-order"],
    "finance": ["valuation", "dcf", "lbo", "comps", "earnings", "financial model",
                "10-k", "10-q", "ebitda", "balance sheet", "portfolio", "kyc"],
    "ledger": ["dataset", "metric", "spreadsheet", "compute the", "statistics",
               "data analysis", "regression"],
}


@dataclass
class RouteDecision:
    shape: str  # single | swarm
    profile: str  # the lead profile
    rationale: str
    candidates: List[str] = field(default_factory=list)


def _scores(goal_lower: str) -> dict:
    return {p: sum(1 for kw in kws if kw in goal_lower) for p, kws in _KEYWORDS.items()}


def route(goal: str, *, available_profiles: List[str], default_profile: str = "coder") -> RouteDecision:
    """Pick the lead profile and shape for a goal. Deterministic; safe fallback."""
    avail = set(available_profiles)
    goal_lower = (goal or "").lower()
    matched = {p: s for p, s in _scores(goal_lower).items() if s > 0 and p in avail}

    if not matched:
        prof = default_profile if default_profile in avail else (
            sorted(avail)[0] if avail else default_profile)
        return RouteDecision("single", prof,
                             "no domain keyword matched; safe fallback to a single agent")

    ranked = sorted(matched.items(), key=lambda kv: (-kv[1], kv[0]))
    top_profile = ranked[0][0]
    domains_hit = [p for p, _ in ranked]

    if len(domains_hit) > 1:
        return RouteDecision("swarm", top_profile,
                             f"multiple domains matched ({', '.join(sorted(domains_hit))}); "
                             f"fan out with {top_profile} leading",
                             candidates=domains_hit)
    return RouteDecision("single", top_profile,
                         f"single domain matched: {top_profile}", candidates=[top_profile])


def route_and_assign(
    conn,
    goal: str,
    *,
    available_profiles: List[str],
    board: str = "default",
    default_profile: str = "coder",
) -> Tuple[RouteDecision, str]:
    """Route a goal and create a Kanban card assigned to the chosen lead profile.
    Returns (decision, task_id). For a swarm, the lead gets the card; decomposing
    the rest across the candidate profiles is the Stage-2 brain's job (open item)."""
    from . import kanban_bridge

    decision = route(goal, available_profiles=available_profiles, default_profile=default_profile)
    task_id = kanban_bridge.assign_card(
        conn, title=goal[:120] or "task", profile=decision.profile, body=goal, board=board)
    return decision, task_id
