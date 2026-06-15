"""Proactivity — track events the user attends and surface warm check-ins.

Ported from OpenComputer v1's proactivity subsystem into the v2 (hermes-agent) idiom
as a plugin. The agent tracks events the user tells it about; after an event ends it
surfaces a warm in-context check-in ("how'd X go?"), learns the reply into MEMORY.md,
and adapts its reminder cadence from feedback.

PROTECTED INVARIANT (from v1): proactive surfacing is **default-OFF / consent-gated**.
Installing the plugin does nothing until ``proactivity.enabled: true`` (or
``hermes proactivity enable``). Surfacing is in-context only (via the ``pre_llm_call``
hook) — there is no out-of-band push in this port, so the user is never messaged
unprompted. Only TOLD_FACT / USER_LOOP sensitivities are ever push-eligible; SENSITIVE
events are hard-suppressed.
"""

from __future__ import annotations

import logging
import re
import time
import uuid

logger = logging.getLogger("hermes.plugins.proactivity")

_SLASH_HELP = """Proactivity — event check-ins. Subcommands:
  /track <title>              track an event that just ended (check-in owed)
  /track <title> in <2h|30m|1d>   track an upcoming event
  /proactivity                show status + tracked events
  /proactivity enable|disable hint: set proactivity.enabled in config.yaml"""

_DUR_RE = re.compile(r"\bin\s+(\d+)\s*(m|min|mins|h|hr|hrs|hour|hours|d|day|days)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Profile-scoped paths
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


def _store():
    from .store import ProactivityStore

    return ProactivityStore(_home_dir() / "proactivity.db")


# ---------------------------------------------------------------------------
# /track parsing
# ---------------------------------------------------------------------------
def _parse_duration_seconds(unit_count: int, unit: str) -> float:
    u = unit.lower()
    if u.startswith("m"):
        return unit_count * 60.0
    if u.startswith("h"):
        return unit_count * 3600.0
    return unit_count * 86400.0  # days


def _parse_track(raw_args: str):
    """Return (title, ends_at) from ``/track`` args, or (None, None) if no title."""
    text = (raw_args or "").strip()
    if not text:
        return None, None
    now = time.time()
    ends_at = now  # default: just ended -> check-in owed
    m = _DUR_RE.search(text)
    if m:
        secs = _parse_duration_seconds(int(m.group(1)), m.group(2))
        ends_at = now + secs
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
        id=uuid.uuid4().hex[:12],
        title=title,
        starts_at=now,
        ends_at=ends_at,
        source="user_told",
        sensitivity=Sensitivity.TOLD_FACT,
        state=state,
        created_at=now,
    )
    try:
        _store().add_event(ev)
    except Exception as exc:  # noqa: BLE001
        return f"Could not track event: {type(exc).__name__}: {exc}"
    when = "now (check-in owed)" if ends_at <= now else f"in {int((ends_at - now) / 60)} min"
    return f"Tracking '{title}' — ends {when}. I'll check in afterward (if proactivity is enabled)."


def _handle_proactivity(raw_args: str):
    from .config import load_proactivity_config

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"
    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP
    if sub in ("enable", "disable"):
        want = sub == "enable"
        return (
            f"To {'enable' if want else 'disable'} proactivity, set "
            f"`proactivity.enabled: {str(want).lower()}` in config.yaml "
            f"(or run `hermes proactivity {sub}`). This is consent-gated by design."
        )

    cfg = load_proactivity_config()
    try:
        events = _store().all_events()
    except Exception:  # noqa: BLE001
        events = []
    lines = [
        "Proactivity (event check-ins)",
        f"  enabled: {cfg.enabled}   push cap/day: {cfg.push_cap_per_day}   "
        f"quiet: {cfg.quiet_start_hour}:00–{cfg.quiet_end_hour}:00",
        f"  tracked events: {len(events)}",
    ]
    for ev in events[:10]:
        lines.append(f"    [{ev.state.value}] {ev.title}")
    if not cfg.enabled:
        lines.append("  (disabled — surfacing is OFF until you opt in. This is by design.)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# pre_llm_call: capture reply -> writeback -> apply feedback -> surface
# ---------------------------------------------------------------------------
def _on_pre_llm_call(**kwargs):
    try:
        from datetime import datetime

        from . import surface, writeback
        from .cadence import load_cadence
        from .config import load_proactivity_config
        from .models import EventContext

        config = load_proactivity_config()
        if not config.enabled:
            return None

        store = _store()
        home = _home_dir()
        tuning = load_cadence(home)
        now = time.time()
        local_hour = datetime.now().hour
        user_message = kwargs.get("user_message") or ""

        # 1) Capture a reply to a PRIOR check-in, then learn it.
        captured = surface.capture_reply(store, user_message, now)
        if captured:
            writeback.write_checkin_reply(captured[0], captured[1])

        # 2) Adapt cadence from the user's words.
        tuning = surface.apply_feedback(config, tuning, home, user_message, now)

        # 3) Surface at most one due check-in, in-context only.
        line = surface.build_injection(
            store,
            config,
            tuning,
            now=now,
            local_hour=local_hour,
            opened_conversation_since_end=True,  # the user is actively chatting
            ctx=EventContext(),
        )
        if line:
            return {"context": f"[proactive check-in] {line}"}
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
    """Plugin entry point — wire the surfacing hook and commands."""
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)

    ctx.register_command(
        "track",
        _handle_track,
        description="Track an event for a later check-in",
        args_hint="<title> [in 2h]",
    )
    ctx.register_command(
        "proactivity",
        _handle_proactivity,
        description="Show proactivity status and tracked events",
        args_hint="[status|enable|disable]",
    )
    try:
        ctx.register_cli_command(
            "proactivity",
            help="Event check-ins: status, track, enable/disable",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Proactive event tracking and check-ins",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactivity: could not register CLI command: %s", exc)

    logger.debug("proactivity plugin registered")
