"""Proactivity — a general, source-agnostic engine for a proactive agent.

Ported and substantially redesigned from OpenComputer v1. Proactivity is a PIPELINE,
not a sensor list: pluggable sources emit ``ProactiveMoment``s → a deterministic gate
(motivation × timing × budget × quiet-hours) → delivery (in-context, or out-of-band
push / digest THROUGH THE GATEWAY). Calendar/Luma-style sensors are just future plugins
behind the same interface; the richest source this agent owns is its own conversation
history (commitment extraction — "you said you'd do X").

PROTECTED INVARIANT (from v1): proactive surfacing is **default-OFF / consent-gated**.
Installing the plugin does nothing until ``proactivity.enabled: true``. In-context
surfacing rides the ``pre_llm_call`` hook (free, preferred); out-of-band push only ever
happens for urgent, push-eligible moments that clear the full gate, delivered through the
gateway's outbound path. SENSITIVE is hard-suppressed; re-engagement never pushes.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

logger = logging.getLogger("hermes.plugins.proactivity")

_DUR_RE = re.compile(r"\bin\s+(\d+)\s*(m|min|mins|h|hr|hrs|hour|hours|d|day|days)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Profile-scoped paths + engine assembly
# ---------------------------------------------------------------------------
def _home_dir():
    from pathlib import Path

    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "proactivity"
    except Exception:  # noqa: BLE001
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "proactivity"


def _event_store():
    from .store import ProactivityStore

    return ProactivityStore(_home_dir() / "proactivity.db")


def _moment_store():
    from .moment_store import MomentStore

    return MomentStore(_home_dir() / "moments.db")


def _engine(config):
    from .engine import Engine

    return Engine(_home_dir(), _event_store(), _moment_store(), config)


# ---------------------------------------------------------------------------
# /track parsing (kept)
# ---------------------------------------------------------------------------
def _parse_duration_seconds(unit_count: int, unit: str) -> float:
    u = unit.lower()
    if u.startswith("m"):
        return unit_count * 60.0
    if u.startswith("h"):
        return unit_count * 3600.0
    return unit_count * 86400.0


def _parse_track(raw_args: str):
    text = (raw_args or "").strip()
    if not text:
        return None, None
    now = time.time()
    ends_at = now
    m = _DUR_RE.search(text)
    if m:
        ends_at = now + _parse_duration_seconds(int(m.group(1)), m.group(2))
        text = (text[: m.start()] + text[m.end():]).strip()
    title = text.strip().strip('"').strip()
    if not title:
        return None, None
    return title, ends_at


def _handle_track(raw_args: str):
    from .models import EventState, Sensitivity, TrackedEvent

    title, ends_at = _parse_track(raw_args)
    if not title or ends_at is None:
        return "Usage: /track <title> [in 2h|30m|1d]"
    now = time.time()
    state = EventState.TRACKED if ends_at > now else EventState.PENDING
    ev = TrackedEvent(
        id=uuid.uuid4().hex[:12], title=title, starts_at=now, ends_at=ends_at,
        source="user_told", sensitivity=Sensitivity.TOLD_FACT, state=state, created_at=now,
    )
    try:
        _event_store().add_event(ev)
    except Exception as exc:  # noqa: BLE001
        return f"Could not track event: {type(exc).__name__}: {exc}"
    when = "now (check-in owed)" if ends_at <= now else f"in {int((ends_at - now) / 60)} min"
    return f"Tracking '{title}' — ends {when}. I'll check in afterward (if proactivity is enabled)."


def _handle_proactivity(raw_args: str):
    from .config import load_proactivity_config

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"
    if sub in ("help", "-h", "--help"):
        return (
            "Proactivity — a proactive agent. Subcommands:\n"
            "  /proactivity              status\n"
            "  /track <title> [in 2h]    track an event for a check-in\n"
            "  /commitments              show commitments I'm tracking from our chats\n"
            "  hermes proactivity run    poll sources + deliver push/digest (background)\n"
            "  hermes proactivity enable enable proactive surfacing (consent)"
        )
    if sub in ("enable", "disable"):
        want = sub == "enable"
        return (
            f"To {'enable' if want else 'disable'} proactivity set "
            f"`proactivity.enabled: {str(want).lower()}` in config.yaml "
            f"(or run `oc proactivity {sub}`). Consent-gated by design."
        )

    cfg = load_proactivity_config()
    try:
        events = _event_store().all_events()
        counts = _moment_store().counts_by_state()
    except Exception:  # noqa: BLE001
        events, counts = [], {}
    lines = [
        "Proactivity (a proactive agent)",
        f"  enabled: {cfg.enabled}   push budget/day: {cfg.push_cap_per_day}   "
        f"quiet: {cfg.quiet_start_hour}:00–{cfg.quiet_end_hour}:00",
        f"  sources: commitment, event-tracker, inactivity",
        f"  moments: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none yet"),
        f"  tracked events: {len(events)}",
    ]
    if not cfg.enabled:
        lines.append("  (disabled — surfacing is OFF until you opt in. By design.)")
    return "\n".join(lines)


def _handle_commitments(raw_args: str):
    from .moment import Category

    try:
        moments = _moment_store().all_moments(limit=50)
    except Exception:  # noqa: BLE001
        moments = []
    commits = [m for m in moments if m.category is Category.COMMITMENT]
    if not commits:
        return "No commitments tracked yet. I extract these from our conversations when proactivity runs."
    lines = ["Commitments I'm tracking from our conversations:"]
    for m in commits[:15]:
        lines.append(f"  [{m.state.value}] {m.title}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background cycle (poll all sources -> push urgent + digest) — the gateway path
# ---------------------------------------------------------------------------
def run_background_cycle(*, deliver_digest: bool = False, adapters=None, loop=None) -> dict:
    """One full proactivity cycle. Safe to call from the CLI or a cron job.

    Returns a summary dict. Delivery goes out through the gateway's outbound path.
    """
    import asyncio
    from datetime import datetime

    from .config import load_proactivity_config
    from .session_reader import default_state_db

    cfg = load_proactivity_config()
    if not cfg.enabled:
        return {"enabled": False}
    eng = _engine(cfg)
    now = time.time()
    local_hour = datetime.now().hour
    state_db = default_state_db()
    summary = asyncio.run(
        eng.run_background(now, state_db, local_hour=local_hour, adapters=adapters, loop=loop)
    )
    if deliver_digest:
        summary["digest_delivered"] = eng.deliver_digest(now, adapters=adapters, loop=loop)
    return summary


# ---------------------------------------------------------------------------
# pre_llm_call: capture reply -> writeback -> feedback -> surface in-context
# ---------------------------------------------------------------------------
def _on_pre_llm_call(**kwargs):
    try:
        from datetime import datetime

        from . import surface, writeback
        from .cadence import load_cadence
        from .config import load_proactivity_config

        config = load_proactivity_config()
        if not config.enabled:
            return None

        now = time.time()
        local_hour = datetime.now().hour
        user_message = kwargs.get("user_message") or ""
        eng = _engine(config)

        # 1) Capture a reply to a PRIOR surfaced moment, then learn it.
        captured = eng.capture_reply(user_message, now)
        if captured:
            writeback.write_checkin_reply(captured[0], captured[1])

        # 2) Adapt cadence from the user's words (mute / more / fewer).
        try:
            surface.apply_feedback(config, load_cadence(_home_dir()), _home_dir(), user_message, now)
        except Exception:  # noqa: BLE001
            pass

        # 3) Surface at most one due moment, in-context.
        m = eng.surface_in_context(now, local_hour=local_hour)
        if m is not None:
            note = f"[proactive] {m.body}"
            if m.reasoning:
                note += f"\n(why: {m.reasoning})"
            return {"context": note}
    except Exception as exc:  # noqa: BLE001 — hooks are fail-open
        logger.debug("proactivity: pre_llm_call failed: %s", exc)
    return None


# ---------------------------------------------------------------------------
# CLI: hermes proactivity ...
# ---------------------------------------------------------------------------
def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def register(ctx) -> None:
    """Plugin entry point — wire the surfacing hook, the aux task, and commands."""
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    try:
        ctx.register_auxiliary_task(
            key="proactivity",
            display_name="Proactivity extraction",
            description="Extracts commitments/follow-ups from conversation for proactive surfacing",
            defaults={"provider": "auto", "timeout": 30},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactivity: could not register auxiliary task: %s", exc)

    ctx.register_command("track", _handle_track,
                         description="Track an event for a later check-in", args_hint="<title> [in 2h]")
    ctx.register_command("proactivity", _handle_proactivity,
                         description="Show proactivity status", args_hint="[status|enable|disable]")
    ctx.register_command("commitments", _handle_commitments,
                         description="Show commitments tracked from conversations")
    try:
        ctx.register_cli_command(
            "proactivity",
            help="Proactive agent: status, track, run, enable/disable",
            setup_fn=_cli_setup, handler_fn=_cli_handle,
            description="A general proactivity engine (commitments, events, check-ins)",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactivity: could not register CLI command: %s", exc)

    logger.debug("proactivity plugin registered")
