"""Event-tracker source — bridges the user-tracked events (`/track`) into the pipeline.

The events the user explicitly asks the agent to track are just one proactive source:
when an event ends, emit a warm follow-up check-in moment. (Calendar/Luma/email would be
sibling sources behind the same interface — none are the core.)
"""

from __future__ import annotations

from ..gate import render_checkin
from ..moment import Category, ProactiveMoment
from ..models import EventState
from ..store import ProactivityStore
from .base import PollContext


class EventTrackerSource:
    id = "event_tracker"

    def __init__(self, store: ProactivityStore) -> None:
        self._store = store

    def available(self) -> bool:
        return True

    async def poll(self, ctx: PollContext) -> list[ProactiveMoment]:
        return self.collect(ctx.now)

    def collect(self, now: float) -> list[ProactiveMoment]:
        """Synchronous check-in collection (no I/O await) — usable from the per-turn
        hook so event check-ins surface promptly even without the background cron."""
        # Promote events whose end time has passed; emit a check-in for each pending one.
        self._store.promote_ended_to_pending(now)
        moments: list[ProactiveMoment] = []
        for ev in self._store.pending():
            moments.append(
                ProactiveMoment(
                    id=f"event:{ev.id}",
                    source_id=self.id,
                    category=Category.FOLLOW_UP,
                    title=ev.title,
                    body=render_checkin(ev),
                    reasoning="You tracked this event; it has ended.",
                    trigger_at=ev.ends_at,
                    expires_at=ev.ends_at + 14 * 24 * 3600.0,
                    urgency=0.5,
                    sensitivity=ev.sensitivity,
                    confidence=1.0,
                    dedup_key=f"event:{ev.id}",
                    created_at=now,
                    metadata={"event_id": ev.id},
                )
            )
        return moments

    def mark_acked(self, event_id: str, now: float) -> None:
        ev = self._store.get(event_id)
        if ev and ev.state in (EventState.PENDING, EventState.SURFACED, EventState.PUSHED, EventState.TRACKED):
            self._store.mark_acked(event_id, now)
