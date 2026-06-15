"""The deterministic surfacing gate — ported from OpenComputer v1.

A pure decision table (no LLM, no floats) that decides whether a tracked event's
check-in should stay SILENT, surface IN_CONTEXT, or earn a rare out-of-band PUSH.
Hard suppressors come first; PUSH is the narrow, gated exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .models import EventContext, Sensitivity, SurfaceTier, TrackedEvent

# How long after an event ends we prefer to wait for a natural conversation
# before spending the one earned push (hours).
IN_CONTEXT_GRACE_HOURS = 12.0


@dataclass(frozen=True)
class GateInputs:
    sensitivity: Sensitivity
    in_quiet_hours: bool
    pushes_in_window: int
    push_cap: int
    hours_since_end: float
    opened_conversation_since_end: bool
    enabled: bool


def decide_tier(i: GateInputs) -> SurfaceTier:
    """Pure deterministic gate. Order matters: hard suppressors, then push, else in-context."""
    if not i.enabled:
        return SurfaceTier.SILENT
    if i.sensitivity is Sensitivity.SENSITIVE:
        return SurfaceTier.SILENT  # INVARIANT 4: hard-suppress

    # PUSH is the rare exception: only after the in-context grace lapses with no
    # natural conversation, only for push-eligible sensitivity, never in quiet
    # hours, never over the cap. (INVARIANT 1 enforced by push_eligible.)
    grace_lapsed = i.hours_since_end >= IN_CONTEXT_GRACE_HOURS
    if (
        grace_lapsed
        and not i.opened_conversation_since_end
        and i.sensitivity.push_eligible
        and not i.in_quiet_hours
        and i.pushes_in_window < i.push_cap
    ):
        return SurfaceTier.PUSH

    return SurfaceTier.IN_CONTEXT


def render_checkin(ev: TrackedEvent, ctx: Optional[EventContext] = None) -> str:
    """Warm, knows-you voice. Provenance is NEVER volunteered (INVARIANT 2).

    Personalisation is name-only. Without attendance confirmation, assume nothing.
    """
    name = ctx.name if ctx else ""
    if ev.attended_confirmed:
        if name:
            return (
                f"{name}, how'd {ev.title} go? Anything worth remembering — "
                f"people, ideas, how it felt?"
            )
        return (
            f"How'd {ev.title} go? Anything worth remembering — "
            f"people, ideas, how it felt?"
        )
    # Assume-nothing: a question that's fine whether or not they went.
    if name:
        return (
            f"Hey {name} — did you make it to {ev.title}? "
            f"If you went, I'd love to hear how it landed."
        )
    return (
        f"Hey — did you make it to {ev.title}? "
        f"If you went, I'd love to hear how it landed."
    )


def render_register_assist(ev: TrackedEvent, ctx: Optional[EventContext] = None) -> str:
    """Warm register-assist for a freshly DISCOVERED event. Byte-stable.

    The taste claim ("your kind of thing") is only made when ``ctx`` has actual
    backing history.
    """
    if ctx is None:
        return (
            f"There's {ev.title} coming up that looks like your kind of thing — "
            f"want me to register you?"
        )
    lead = f"Hey {ctx.name} — there's" if ctx.name else "There's"
    if ctx.has_history:
        return (
            f"{lead} {ev.title} coming up that looks like your kind of thing — "
            f"want me to register you?"
        )
    return f"{lead} {ev.title} coming up — want me to register you?"
