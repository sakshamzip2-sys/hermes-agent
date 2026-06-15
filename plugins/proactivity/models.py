"""Proactivity data model — ported verbatim from OpenComputer v1.

A ``TrackedEvent`` is something the user is attending (told the agent about, or the
agent registered them for). After it ends, a warm check-in is owed. The sensitivity
enum encodes the privacy invariant: only told-facts and user-initiated loops may ever
trigger an out-of-band push.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Optional


class EventState(str, enum.Enum):
    DISCOVERED = "discovered"  # a sensor found it; awaiting the user's register decision
    TRACKED = "tracked"        # the user is going (told / registered); not yet ended
    PENDING = "pending"        # ended; a check-in is owed
    SURFACED = "surfaced"      # shown in-context
    PUSHED = "pushed"          # delivered out-of-band
    ACKED = "acked"            # user responded / loop closed
    EXPIRED = "expired"        # TTL passed without surfacing


class Sensitivity(str, enum.Enum):
    TOLD_FACT = "told_fact"        # the user told the agent / the agent registered them
    USER_LOOP = "user_loop"        # the user explicitly asked the agent to follow up
    INFERRED_SELF = "inferred_self"
    RELATIONAL = "relational"
    THIRD_PARTY = "third_party"
    SENSITIVE = "sensitive"        # funeral / interview / flagged — hard-suppress

    @property
    def push_eligible(self) -> bool:
        # INVARIANT 1: only told-facts and user-initiated loops may ever push.
        return self in (Sensitivity.TOLD_FACT, Sensitivity.USER_LOOP)


class SurfaceTier(str, enum.Enum):
    SILENT = "silent"
    IN_CONTEXT = "in_context"
    PUSH = "push"


@dataclass(frozen=True)
class EventContext:
    """Minimal memory-aware context for rendering a warm check-in.

    ``name`` lets the agent address the user by name (warmth, not a provenance
    claim). ``has_history`` gates the "your kind of thing" taste claim so the
    agent never asserts a preference it can't back.
    """

    name: str = ""
    has_history: bool = False


@dataclass
class TrackedEvent:
    id: str
    title: str
    starts_at: float
    ends_at: float
    source: str                       # "agent_registered" | "user_told" | ...
    url: Optional[str] = None
    sensitivity: Sensitivity = Sensitivity.TOLD_FACT
    attended_confirmed: bool = False  # drives the mis-fire firewall phrasing
    state: EventState = EventState.TRACKED
    surfaced_at: Optional[float] = None
    pushed_at: Optional[float] = None
    acked_at: Optional[float] = None
    created_at: float = 0.0

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "source": self.source,
            "url": self.url,
            "sensitivity": self.sensitivity.value,
            "attended_confirmed": int(self.attended_confirmed),
            "state": self.state.value,
            "surfaced_at": self.surfaced_at,
            "pushed_at": self.pushed_at,
            "acked_at": self.acked_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TrackedEvent":
        return cls(
            id=row["id"],
            title=row["title"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            source=row["source"],
            url=row["url"],
            sensitivity=Sensitivity(row["sensitivity"]),
            attended_confirmed=bool(row["attended_confirmed"]),
            state=EventState(row["state"]),
            surfaced_at=row["surfaced_at"],
            pushed_at=row["pushed_at"],
            acked_at=row["acked_at"],
            created_at=row["created_at"],
        )
