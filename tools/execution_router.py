"""Backend-agnostic execution-mode router.

Answers the question "how should this task run?" as a deterministic POLICY layer
over the sandbox primitives — WITHOUT coupling to any backend (docker today, e2b
later) and WITHOUT defaulting everything to an expensive dedicated sandbox.

Modes (each maps to a primitive that already exists in v2):
  - ``inline``    : run in the current/shared sandbox. The cheap DEFAULT.
  - ``isolated``  : run in a dedicated per-task sandbox
                    (→ ``delegation.subagent_sandbox: isolated``).
  - ``durable``   : long-running, resumable; persist a sandbox handle so the
                    session can reattach after a restart (→ the durable-session
                    reconnect seam: BaseEnvironment.handle/reconnect +
                    SessionDB.set/get_session_sandbox_handle).
  - ``scheduled`` : recurring / unattended → hand to cron.

Design principles (deliberately the INVERSE of a "route everything to a microVM"
router):
  * Cheapest correct mode by default; ESCALATE only on explicit signals.
  * Decide a *mode*, never a backend — backends are resolved downstream.
  * Honest, explainable output (``reasons`` + a categorical ``basis``) instead of
    a fabricated confidence float.
  * A helper the agent/delegation layer CONSULTS — not a mandatory gate.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

VALID_MODES = ("inline", "isolated", "durable", "scheduled")

# A duration hint above this many minutes implies the work wants to survive a
# restart → durable. (Recurrence, handled separately, implies scheduled.)
DURABLE_MINUTES_THRESHOLD = 10

# Recurrence — the strongest shape; checked first.
_SCHEDULED_SIGNALS = (
    "every day", "daily", "weekly", "hourly", "nightly", "each morning",
    "each night", "periodically", "recurring", "schedule", "cron",
    "every hour", "every minute", "every 15 minute", "every 30 minute",
    "every few minutes", "on a schedule", "twice a day",
)

# Long-running / stateful / resumable → durable.
_DURABLE_SIGNALS = (
    "for hours", "hours", "long-running", "long running", "overnight",
    "until complete", "until done", "run until", "resume", "resumable",
    "checkpoint", "keep running", "stateful", "persist", "multi-hour",
    "several hours", "all night",
)

# Dedicated / parallel / sandboxed → isolated.
_ISOLATED_SIGNALS = (
    "isolated", "in isolation", "dedicated sandbox", "own sandbox", "sandboxed",
    "parallel", "in parallel", "independent", "clean environment",
    "separate environment", "concurrent",
)

_MODE_CONFIG: Dict[str, Dict[str, Any]] = {
    "inline": {},
    "isolated": {"subagent_sandbox": "isolated"},
    "durable": {"persist_sandbox_handle": True},
    "scheduled": {"use_cron": True},
}


@dataclass
class RouteDecision:
    """The router's verdict. ``basis`` is categorical and truthful:
    ``explicit`` (a hint forced it), ``signal`` (a heuristic matched), or
    ``default`` (nothing matched → cheapest mode)."""

    mode: str
    basis: str
    reasons: List[str] = field(default_factory=list)
    duration_hint_minutes: Optional[int] = None
    suggested_config: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _matches(text: str, signals) -> List[str]:
    return [s for s in signals if s in text]


def _decide(mode: str, basis: str, reasons: List[str], duration: Optional[int]) -> RouteDecision:
    return RouteDecision(
        mode=mode,
        basis=basis,
        reasons=reasons,
        duration_hint_minutes=duration,
        suggested_config=dict(_MODE_CONFIG[mode]),
    )


def route_execution_mode(
    goal: str,
    *,
    context: str = "",
    hints: Optional[Dict[str, Any]] = None,
) -> RouteDecision:
    """Decide the execution mode for *goal*.

    ``hints`` may include: ``mode`` (force a valid mode), ``expected_duration_minutes``
    (int), ``recurring`` (bool), ``durable`` (bool), ``isolated`` (bool).
    Heuristic keyword matching is a *suggestion*; every match is recorded in
    ``reasons`` so a caller (or reviewer) can see exactly why.
    """
    hints = hints or {}
    text = f"{goal} {context}".lower()
    duration = hints.get("expected_duration_minutes")
    if not isinstance(duration, (int, float)):
        duration = None
    else:
        duration = int(duration)

    # 1. Explicit override wins (only if it names a valid mode).
    forced = hints.get("mode")
    if isinstance(forced, str) and forced in VALID_MODES:
        return _decide(forced, "explicit", [f"explicit hint mode={forced}"], duration)

    # 2. Scheduled — recurrence is the strongest shape.
    sched = _matches(text, _SCHEDULED_SIGNALS)
    if hints.get("recurring") or sched:
        reasons = [f"recurring signal: '{s}'" for s in sched]
        if hints.get("recurring"):
            reasons.append("hint recurring=True")
        return _decide("scheduled", "signal", reasons, duration)

    # 3. Durable — long-running / stateful / resumable.
    dur = _matches(text, _DURABLE_SIGNALS)
    long_by_hint = duration is not None and duration > DURABLE_MINUTES_THRESHOLD
    if hints.get("durable") or dur or long_by_hint:
        reasons = [f"durable signal: '{s}'" for s in dur]
        if long_by_hint:
            reasons.append(
                f"duration {duration}min > {DURABLE_MINUTES_THRESHOLD}min threshold"
            )
        if hints.get("durable"):
            reasons.append("hint durable=True")
        return _decide("durable", "signal", reasons, duration)

    # 4. Isolated — dedicated / parallel work.
    iso = _matches(text, _ISOLATED_SIGNALS)
    if hints.get("isolated") or iso:
        reasons = [f"isolation signal: '{s}'" for s in iso]
        if hints.get("isolated"):
            reasons.append("hint isolated=True")
        return _decide("isolated", "signal", reasons, duration)

    # 5. Default — cheapest correct mode.
    return _decide(
        "inline",
        "default",
        ["no escalation signals; cheapest correct mode"],
        duration,
    )
