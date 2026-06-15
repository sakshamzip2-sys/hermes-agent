"""The proactivity engine — orchestrates sources → store → gate → delivery.

Two cost tiers, deliberately separated:
  - **In-context (per turn, cheap):** the hook calls :meth:`surface_in_context`, which
    only does a synchronous event refresh + reads already-discovered moments and gates
    one to inject. No LLM, no network — safe to run every turn.
  - **Background (cron, full):** :meth:`run_background` runs ALL sources (including the
    LLM commitment extractor), gates each moment for out-of-band delivery, pushes the
    urgent ones through the gateway, and drains the rest into a digest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import gateway_delivery
from .config import ProactivityConfig
from .moment import Category, MomentState, ProactiveMoment
from .moment_gate import Decision, GateInputs, decide
from .moment_store import MomentStore
from .sources.base import PollContext, run_sources
from .sources.commitment import CommitmentSource
from .sources.event_tracker import EventTrackerSource
from .sources.inactivity import InactivitySource
from .store import ProactivityStore

logger = logging.getLogger("hermes.plugins.proactivity.engine")

_DAY = 86400.0


class Engine:
    def __init__(self, home: Path, event_store: ProactivityStore, moment_store: MomentStore,
                 config: ProactivityConfig) -> None:
        self.home = home
        self.event_store = event_store
        self.moments = moment_store
        self.config = config
        self._event_source = EventTrackerSource(event_store)

    def _full_sources(self) -> list:
        return [
            CommitmentSource(),
            self._event_source,
            InactivitySource(quiet_days=float(self.config.inactivity_days)),
        ]

    # -- cheap, per-turn path ----------------------------------------------

    def _cheap_refresh(self, now: float) -> None:
        """Sync: promote ended tracked events into moments (no LLM/network)."""
        for m in self._event_source.collect(now):
            self.moments.upsert(m)
        self.moments.expire_stale(now)

    def surface_in_context(self, now: float, *, local_hour: int) -> Optional[ProactiveMoment]:
        """Gate pending moments for in-context surfacing; mark + return one (or None)."""
        if not self.config.enabled:
            return None
        self._cheap_refresh(now)
        gi = GateInputs(
            in_active_conversation=True,
            in_quiet_hours=self.config.in_quiet_hours(local_hour),
            pushes_today=self.moments.pushes_since(now - _DAY),
            push_cap=self.config.push_cap_per_day,
            now=now,
            min_motivation=self.config.min_motivation,
        )
        for m in self.moments.pending():
            d = decide(m, gi)
            if d is Decision.INJECT:
                self.moments.set_state(m.dedup_key, MomentState.SURFACED, surfaced_at=now)
                return m
            if d is Decision.DIGEST:
                self.moments.set_state(m.dedup_key, MomentState.DIGEST)
            elif d is Decision.DROP:
                self.moments.set_state(m.dedup_key, MomentState.EXPIRED)
        return None

    # -- full, background path ---------------------------------------------

    async def run_background(self, now: float, state_db: Optional[Path], *, local_hour: int,
                             adapters=None, loop=None) -> dict:
        """Poll all sources, gate for out-of-band delivery, push urgent + digest rest."""
        if not self.config.enabled:
            return {"polled": 0, "pushed": 0, "digested": 0}
        ctx = PollContext(now=now, home=self.home, state_db=state_db,
                          recent_window_seconds=self.config.recent_window_days * _DAY)
        candidates = await run_sources(self._full_sources(), ctx)
        new_count = 0
        for m in candidates:
            if self.moments.upsert(m):
                new_count += 1
        self.moments.expire_stale(now)

        gi = GateInputs(
            in_active_conversation=False,
            in_quiet_hours=self.config.in_quiet_hours(local_hour),
            pushes_today=self.moments.pushes_since(now - _DAY),
            push_cap=self.config.push_cap_per_day,
            now=now,
            min_motivation=self.config.min_motivation,
        )
        pushed = digested = 0
        for m in self.moments.pending():
            d = decide(m, gi)
            if d is Decision.PUSH:
                text = self._format_push(m)
                if gateway_delivery.deliver(text, adapters=adapters, loop=loop):
                    self.moments.set_state(m.dedup_key, MomentState.DELIVERED, delivered_at=now)
                    self.moments.record_send(now, "push")
                    pushed += 1
                    gi = GateInputs(  # refresh budget after a send
                        in_active_conversation=False, in_quiet_hours=gi.in_quiet_hours,
                        pushes_today=gi.pushes_today + 1, push_cap=gi.push_cap, now=now,
                        min_motivation=gi.min_motivation,
                    )
                else:
                    self.moments.set_state(m.dedup_key, MomentState.DIGEST)
                    digested += 1
            elif d is Decision.DIGEST:
                self.moments.set_state(m.dedup_key, MomentState.DIGEST)
                digested += 1
            elif d is Decision.DROP:
                self.moments.set_state(m.dedup_key, MomentState.EXPIRED)
        return {"polled": new_count, "pushed": pushed, "digested": digested}

    # -- digest -------------------------------------------------------------

    def build_digest(self, now: float, *, mark: bool = True) -> Optional[str]:
        queue = self.moments.digest_queue()
        if not queue:
            return None
        lines = ["Here's what I've been holding for you:"]
        for m in queue:
            lines.append(f"• {m.body}")
            if mark:
                self.moments.set_state(m.dedup_key, MomentState.DELIVERED, delivered_at=now)
        return "\n".join(lines)

    def deliver_digest(self, now: float, *, adapters=None, loop=None) -> bool:
        text = self.build_digest(now, mark=False)
        if not text:
            return False
        if gateway_delivery.deliver(text, adapters=adapters, loop=loop):
            for m in self.moments.digest_queue():
                self.moments.set_state(m.dedup_key, MomentState.DELIVERED, delivered_at=now)
            self.moments.record_send(now, "digest")
            return True
        return False

    # -- reply capture ------------------------------------------------------

    def capture_reply(self, user_message: str, now: float, *, window_seconds: float = 7200.0):
        """If a surfaced moment awaits a reply (within the window), ack it and return
        ``(title, reply)`` for writeback. Also acks the underlying event, if any."""
        msg = (user_message or "").strip()
        if not msg:
            return None
        awaiting = self.moments.awaiting_reply()
        if not awaiting:
            return None
        m = awaiting[0]
        shown = m.surfaced_at or m.delivered_at or 0.0
        if not shown or (now - shown) > window_seconds:
            return None
        self.moments.set_state(m.dedup_key, MomentState.ACTED, acked_at=now)
        ev_id = m.metadata.get("event_id") if m.metadata else None
        if ev_id:
            try:
                self._event_source.mark_acked(ev_id, now)
            except Exception:  # noqa: BLE001
                pass
        return (m.title, msg)

    @staticmethod
    def _format_push(m: ProactiveMoment) -> str:
        prefix = "⏰ " if m.category in (Category.DEADLINE, Category.COMMITMENT) else "💡 "
        return f"{prefix}{m.body}"
