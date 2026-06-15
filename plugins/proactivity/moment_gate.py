"""The deterministic decision gate for proactive moments.

Pure functions (no LLM, no I/O) so the trust-bearing logic is fully unit-testable.
Synthesizes the research: a motivation score, a per-category threshold, a notification
budget, quiet hours, and channel selection (in-context preferred, push reserved for
urgent + push-eligible, everything else to the digest).

Hard invariants (carried from v1):
  - A moment may PUSH only if ``moment.push_eligible`` (sensitivity AND category).
  - SENSITIVE never surfaces at all.
  - Quiet hours suppress PUSH (degrade to digest), never overridden by score.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .models import Sensitivity
from .moment import Category, ProactiveMoment

# Urgency at/above which an out-of-band push is even considered.
URGENT_THRESHOLD = 0.6

# Per-category base motivation (1..5) — how inherently worth-surfacing the category is.
_CATEGORY_BASE = {
    Category.COMMITMENT: 4,
    Category.DEADLINE: 5,
    Category.PRE_EVENT: 4,
    Category.FOLLOW_UP: 3,
    Category.ANOMALY: 5,
    Category.SUGGESTION: 2,
    Category.HABIT: 2,
    Category.SURFACE_CONTEXT: 3,
    Category.EXTERNAL: 3,
    Category.RE_ENGAGEMENT: 1,
    Category.DIGEST: 1,
}


class Decision(str, enum.Enum):
    DROP = "drop"        # not worth surfacing now (or ever)
    INJECT = "inject"    # surface in-context (free, preferred)
    PUSH = "push"        # deliver out-of-band through the gateway
    DIGEST = "digest"    # hold for the periodic briefing


@dataclass(frozen=True)
class GateInputs:
    in_active_conversation: bool   # is the user chatting right now?
    in_quiet_hours: bool
    pushes_today: int
    push_cap: int
    now: float
    min_motivation: int = 3        # learned/configurable threshold to surface at all


def score_moment(m: ProactiveMoment) -> int:
    """Motivation score in 1..5 — category base nudged by urgency and confidence."""
    base = _CATEGORY_BASE.get(m.category, 2)
    bump = 0
    if m.urgency >= 0.7:
        bump += 1
    if m.confidence < 0.5:
        bump -= 1
    return max(1, min(5, base + bump))


def decide(m: ProactiveMoment, i: GateInputs) -> Decision:
    """Pure routing decision for one moment."""
    # Hard suppress
    if m.sensitivity is Sensitivity.SENSITIVE:
        return Decision.DROP
    if m.expires_at and m.expires_at < i.now:
        return Decision.DROP
    if m.trigger_at and m.trigger_at > i.now:
        return Decision.DROP  # not relevant yet

    if score_moment(m) < i.min_motivation:
        # Below the bar to interrupt — but low-urgency informational items can still
        # ride along in a digest rather than vanish.
        return Decision.DIGEST if m.urgency < 0.3 else Decision.DROP

    # In-context is free and preferred: the user is already here.
    if i.in_active_conversation:
        return Decision.INJECT

    # Out of band: only urgent, push-eligible moments, never in quiet hours, never over cap.
    if not m.push_eligible:
        return Decision.DIGEST
    if i.in_quiet_hours:
        return Decision.DIGEST
    if m.urgency >= URGENT_THRESHOLD and i.pushes_today < i.push_cap:
        return Decision.PUSH
    return Decision.DIGEST
