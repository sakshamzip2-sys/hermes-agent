"""Inactivity / re-engagement source — gentle check-in after a long silence.

Re-engagement is the highest-abuse proactive category (an unsolicited "miss you" push
erodes trust fastest), so it is:
  - category RE_ENGAGEMENT, which is push-FORBIDDEN (never delivered out-of-band) — it
    can only surface in-context or land in a digest.
  - emitted at most once per silence episode (deduped on the day it crosses the threshold).
"""

from __future__ import annotations

import datetime as _dt

from ..moment import Category, ProactiveMoment
from ..models import Sensitivity
from ..session_reader import last_user_message_ts
from .base import PollContext


class InactivitySource:
    id = "inactivity"

    def __init__(self, *, quiet_days: float = 7.0) -> None:
        self.quiet_days = quiet_days

    def available(self) -> bool:
        return True

    async def poll(self, ctx: PollContext) -> list[ProactiveMoment]:
        if not ctx.state_db:
            return []
        last = last_user_message_ts(ctx.state_db)
        if last is None:
            return []
        gap_days = (ctx.now - last) / 86400.0
        if gap_days < self.quiet_days:
            return []
        # Dedup per crossing-day so we don't re-emit every poll during a long silence.
        day = _dt.date.fromtimestamp(ctx.now).isoformat()
        moment_id = f"reengage:{day}"
        return [
            ProactiveMoment(
                id=moment_id,
                source_id=self.id,
                category=Category.RE_ENGAGEMENT,
                title="checking in",
                body="It's been a little while — anything I can help you pick back up?",
                reasoning=f"No messages from you in about {int(gap_days)} days.",
                trigger_at=ctx.now,
                expires_at=ctx.now + 2 * 86400.0,
                urgency=0.2,
                sensitivity=Sensitivity.TOLD_FACT,
                confidence=1.0,
                dedup_key=moment_id,
                created_at=ctx.now,
            )
        ]
