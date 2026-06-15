"""Per-turn proactivity orchestration.

Maps v1's PRE_LLM_CALL injection loop onto v2's ``pre_llm_call`` plugin hook:

1. :func:`capture_reply` — if a check-in was surfaced on a prior turn, treat this
   user message as the reply, close the loop (ACK), and hand the words to writeback.
2. :func:`apply_feedback` — read the user's words for a cadence signal and adapt.
3. :func:`build_injection` — promote ended events, run the deterministic gate, and
   return the one check-in line to inject (or ``None``).

All functions are fail-soft and side-effect-scoped to the injected store/config.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Optional

from .cadence import (
    CadenceTuning,
    effective_push_cap,
    keyword_muted,
    save_cadence,
    step_cap,
)
from .config import ProactivityConfig
from .feedback import classify_feedback
from .gate import GateInputs, decide_tier, render_checkin
from .models import EventContext, SurfaceTier
from .store import ProactivityStore

logger = logging.getLogger("hermes.plugins.proactivity.surface")

_SECONDS_PER_HOUR = 3600.0
_SECONDS_PER_DAY = 86400.0
# Only attribute a user message to a check-in surfaced within this window. Past it,
# an awaiting check-in is considered unanswered (it can re-surface / expire) rather
# than capturing an unrelated later message as its "reply".
_REPLY_WINDOW_SECONDS = 2 * _SECONDS_PER_HOUR


def capture_reply(store: ProactivityStore, user_message: str, now: float) -> Optional[tuple[str, str]]:
    """If a check-in awaits a reply, ACK it and return ``(event_title, reply)``.

    Called BEFORE surfacing so a reply is attributed to the PRIOR check-in, never
    to one surfaced this same turn. Only the most recent awaiting check-in is
    closed, and only if it was surfaced within ``_REPLY_WINDOW_SECONDS`` — so an
    unrelated message hours/days later is NOT mis-captured as a reply.
    """
    if not user_message or not user_message.strip():
        return None
    awaiting = store.surfaced_or_pushed()
    if not awaiting:
        return None
    ev = awaiting[0]
    shown_at = ev.surfaced_at or ev.pushed_at or 0.0
    if not shown_at or (now - shown_at) > _REPLY_WINDOW_SECONDS:
        return None
    store.mark_acked(ev.id, now)
    return (ev.title, user_message.strip())


def apply_feedback(
    config: ProactivityConfig,
    tuning: CadenceTuning,
    home,
    user_message: str,
    now: float,
) -> CadenceTuning:
    """Adapt cadence from the user's words. Subtractive mutes; bounded cap steps."""
    if not config.cadence_evolution:
        return tuning
    signal = classify_feedback(user_message)
    if signal == "none":
        return tuning

    new = tuning
    if isinstance(signal, tuple) and signal[0] == "mute":
        kw = signal[1]
        if kw and kw not in new.muted_keywords:
            new = replace(new, muted_keywords=(*new.muted_keywords, kw))
    elif signal == "too_many":
        current = effective_push_cap(config.push_cap_per_day, new)
        new = replace(new, push_cap=step_cap(current, "down", ceiling=config.push_cap_per_day))
    elif signal == "too_few":
        current = effective_push_cap(config.push_cap_per_day, new)
        new = replace(new, push_cap=step_cap(current, "up", ceiling=config.push_cap_per_day))

    if new is not tuning:
        new = replace(new, decisions=new.decisions + 1, last_recompute_at=now)
        save_cadence(home, new)
    return new


def build_injection(
    store: ProactivityStore,
    config: ProactivityConfig,
    tuning: CadenceTuning,
    *,
    now: float,
    local_hour: int,
    opened_conversation_since_end: bool = True,
    ctx: Optional[EventContext] = None,
) -> Optional[str]:
    """Promote ended events, gate them, and return ONE check-in line (or None).

    At most one check-in is surfaced per turn to avoid overwhelming the user.
    """
    if not config.enabled:
        return None

    store.promote_ended_to_pending(now)

    pending = store.pending()
    if not pending:
        return None

    window_start = now - _SECONDS_PER_DAY
    pushes = store.pushes_since(window_start)
    cap = effective_push_cap(config.push_cap_per_day, tuning)
    in_quiet = config.in_quiet_hours(local_hour)
    ttl_seconds = config.event_ttl_days * _SECONDS_PER_DAY

    for ev in pending:
        # Expire stale check-ins.
        if now - ev.ends_at > ttl_seconds:
            store.mark_expired(ev.id)
            continue
        # Subtractive mute: never surface a muted source/title.
        if keyword_muted(ev.source, ev.title, tuning.muted_keywords):
            continue

        tier = decide_tier(
            GateInputs(
                sensitivity=ev.sensitivity,
                in_quiet_hours=in_quiet,
                pushes_in_window=pushes,
                push_cap=cap,
                hours_since_end=(now - ev.ends_at) / _SECONDS_PER_HOUR,
                opened_conversation_since_end=opened_conversation_since_end,
                enabled=config.enabled,
            )
        )
        if tier is SurfaceTier.SILENT:
            continue

        line = render_checkin(ev, ctx)
        if tier is SurfaceTier.PUSH:
            store.mark_pushed(ev.id, now)
        else:
            store.mark_surfaced(ev.id, now)
        return line

    return None
