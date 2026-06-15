"""ProactiveMoment — the normalized unit every source emits and the gate consumes.

A *source* is dumb: it observes some signal (the user's stated commitments, a tracked
event ending, a long silence, an open topic) and emits candidate ``ProactiveMoment``s.
It never decides whether/when/how to deliver — all policy lives in the gate. This is
the single design decision that keeps proactivity general instead of hardcoded to any
one data source.

Categories follow the taxonomy from the proactive-assistant research (commitment,
deadline, follow-up, habit, re-engagement, digest, …). Sensitivity (reused from
``models``) carries v1's privacy invariant: only TOLD_FACT / USER_LOOP may ever push.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import Sensitivity


class Category(str, enum.Enum):
    """Distinct kinds of proactive moment (from the production-assistant taxonomy)."""

    COMMITMENT = "commitment"        # "you said you'd do X"
    DEADLINE = "deadline"            # a time-bound thing approaches
    PRE_EVENT = "pre_event"          # surface what you'll need just before an event
    FOLLOW_UP = "follow_up"          # re-open a topic the user cared about
    HABIT = "habit"                  # recurring behavior detected -> gentle nudge
    SURFACE_CONTEXT = "surface_context"  # resurface a relevant memory/fact
    RE_ENGAGEMENT = "re_engagement"  # user went quiet (highest abuse risk -> capped)
    SUGGESTION = "suggestion"        # next-best-action
    ANOMALY = "anomaly"              # something is off vs expected
    DIGEST = "digest"                # periodic roll-up
    EXTERNAL = "external"            # outside-world change matched to a stated interest


class MomentState(str, enum.Enum):
    PENDING = "pending"      # emitted, not yet decided
    SURFACED = "surfaced"    # shown in-context
    DIGEST = "digest"        # held for the periodic briefing
    DELIVERED = "delivered"  # pushed out-of-band / included in a digest
    ACTED = "acted"          # user responded / loop closed
    DISMISSED = "dismissed"  # user said "not useful" / muted
    EXPIRED = "expired"      # TTL passed without surfacing


# Categories that may NEVER push out-of-band regardless of sensitivity — they are
# in-context / digest only. Re-engagement is the most abused proactive category; an
# unsolicited "miss you" push erodes trust fastest (documented failure mode).
_PUSH_FORBIDDEN_CATEGORIES = frozenset({Category.RE_ENGAGEMENT})


@dataclass
class ProactiveMoment:
    """A candidate proactive moment. Sources fill the descriptive fields; the gate
    fills ``motivation_score`` and decides routing."""

    id: str
    source_id: str
    category: Category
    title: str                       # short human-facing label
    body: str                        # the message text to surface/deliver
    reasoning: str = ""              # WHY now — shown to the user (decision notice)
    trigger_at: float = 0.0          # earliest epoch-seconds this becomes relevant
    expires_at: float = 0.0          # epoch-seconds TTL; 0 = no expiry
    urgency: float = 0.3             # 0..1 time-sensitivity
    sensitivity: Sensitivity = Sensitivity.TOLD_FACT
    confidence: float = 1.0          # 0..1 confidence in the underlying inference
    dedup_key: str = ""             # collapse duplicate moments across polls
    suggested_action: Optional[str] = None
    state: MomentState = MomentState.PENDING
    created_at: float = 0.0
    surfaced_at: Optional[float] = None
    delivered_at: Optional[float] = None
    acked_at: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def push_eligible(self) -> bool:
        """A moment may push only if its sensitivity allows AND its category isn't
        push-forbidden (re-engagement, etc.)."""
        return self.sensitivity.push_eligible and self.category not in _PUSH_FORBIDDEN_CATEGORIES

    def ensure_dedup_key(self) -> str:
        if not self.dedup_key:
            self.dedup_key = hashlib.sha256(
                f"{self.source_id}|{self.category.value}|{self.title}".encode()
            ).hexdigest()[:16]
        return self.dedup_key

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "category": self.category.value,
            "title": self.title,
            "body": self.body,
            "reasoning": self.reasoning,
            "trigger_at": self.trigger_at,
            "expires_at": self.expires_at,
            "urgency": self.urgency,
            "sensitivity": self.sensitivity.value,
            "confidence": self.confidence,
            "dedup_key": self.ensure_dedup_key(),
            "suggested_action": self.suggested_action,
            "state": self.state.value,
            "created_at": self.created_at,
            "surfaced_at": self.surfaced_at,
            "delivered_at": self.delivered_at,
            "acked_at": self.acked_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ProactiveMoment":
        return cls(
            id=row["id"],
            source_id=row["source_id"],
            category=Category(row["category"]),
            title=row["title"],
            body=row["body"],
            reasoning=row["reasoning"] or "",
            trigger_at=row["trigger_at"] or 0.0,
            expires_at=row["expires_at"] or 0.0,
            urgency=row["urgency"] if row["urgency"] is not None else 0.3,
            sensitivity=Sensitivity(row["sensitivity"]),
            confidence=row["confidence"] if row["confidence"] is not None else 1.0,
            dedup_key=row["dedup_key"] or "",
            suggested_action=row["suggested_action"],
            state=MomentState(row["state"]),
            created_at=row["created_at"] or 0.0,
            surfaced_at=row["surfaced_at"],
            delivered_at=row["delivered_at"],
            acked_at=row["acked_at"],
        )
