"""Dreaming — automatic episodic→declarative memory consolidation for v2.

Ported from OpenComputer v1's three-gate "dreaming" pipeline into the hermes-agent
v2 idiom as a self-contained plugin (capability at the edge — no new core tool).

What it does
------------
After conversations accumulate, dreaming distils durable, user-specific facts from
recent session history and promotes the high-signal ones into ``MEMORY.md`` so the
agent recalls them in future sessions. Each fact passes three gates — importance
(aux-LLM score), recall (did it resurface across sessions?), and diversity (not a
duplicate) — then is promoted, held in ``DREAMS.md``, or dropped.

How it runs
-----------
* Opportunistically on ``on_session_start`` / ``on_session_end`` (debounced to at
  most once per ``dreaming.min_interval_hours``), on a background thread so it
  never delays a turn.
* Manually via the ``/dream`` slash command or ``oc dream run`` CLI command.

Config lives under ``dreaming:`` in config.yaml; the consolidation model is pinned
via ``auxiliary.dreaming``.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.dreaming")

_SLASH_HELP = """Dreaming — memory consolidation. Subcommands:
  /dream            show status
  /dream run        run a consolidation pass now
  /dream run force  bypass the debounce interval
  /dream dreams     list the DREAMS.md holding pen"""


def _on_session_boundary(**kwargs) -> None:
    """Hook: opportunistically trigger a (debounced) dream cycle in the background."""
    try:
        from .runner import maybe_run_in_background

        maybe_run_in_background()
    except Exception as exc:  # noqa: BLE001 — hooks are fail-open
        logger.debug("dreaming: session-boundary trigger failed: %s", exc)


def _handle_slash(raw_args: str):
    """Slash-command handler: ``fn(raw_args: str) -> str | None``."""
    import asyncio

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"

    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP

    if sub == "dreams":
        from . import memory_io

        entries = memory_io.read_dreams_entries()
        if not entries:
            return "DREAMS.md is empty."
        return "DREAMS.md holding pen:\n" + "\n".join(f"  {e}" for e in entries)

    if sub == "run":
        from .runner import run_dream_cycle

        force = len(parts) > 1 and parts[1].lower() in ("force", "--force", "-f")
        try:
            summary = asyncio.run(run_dream_cycle(force=force))
        except Exception as exc:  # noqa: BLE001
            return f"Dream cycle failed: {type(exc).__name__}: {exc}"
        c = summary.counts()
        lines = [
            f"Dream cycle complete: promoted={c['promoted']} updated={c['updated']} "
            f"held={c['held']} dropped={c['dropped']} evaluated={c['evaluated']}"
        ]
        for r in summary.promoted:
            lines.append(f"  + {r.candidate.raw_text}")
        for r in summary.updated:
            lines.append(f"  ~ {r.candidate.raw_text}")
        return "\n".join(lines)

    # default: status
    from .config import load_dreaming_config
    from .llm import aux_client_available
    from .runner import _store_path
    from .store import DreamStore

    cfg = load_dreaming_config()
    store = DreamStore(_store_path())
    last = store.last_run_ts()
    last_str = "never"
    if last:
        import datetime as _dt

        last_str = _dt.datetime.fromtimestamp(last).isoformat(timespec="seconds")
    return (
        "Dreaming (memory consolidation)\n"
        f"  enabled: {cfg.enabled}   aux provider ready: {aux_client_available()}\n"
        f"  min interval: {cfg.min_interval_hours}h   "
        f"score>={cfg.engine.score_threshold}   "
        f"recall>={cfg.engine.min_recall_count} "
        f"({'on' if cfg.engine.recall_gate_enabled else 'off'})   "
        f"diversity<{cfg.engine.diversity_threshold}\n"
        f"  last run: {last_str}\n"
        f"  Use /dream run to consolidate now."
    )


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def register(ctx) -> None:
    """Plugin entry point — wire the auxiliary task, hooks, and commands."""
    # 1) Auxiliary LLM task so users can pin a cheap consolidation model.
    try:
        ctx.register_auxiliary_task(
            key="dreaming",
            display_name="Dreaming consolidation",
            description="Scores/extracts durable facts for MEMORY.md promotion",
            defaults={"provider": "auto", "timeout": 30},
        )
    except Exception as exc:  # noqa: BLE001 — never fail plugin load on this
        logger.debug("dreaming: could not register auxiliary task: %s", exc)

    # 2) Opportunistic, debounced background trigger on session boundaries.
    ctx.register_hook("on_session_start", _on_session_boundary)
    ctx.register_hook("on_session_end", _on_session_boundary)

    # 3) In-session slash command.
    ctx.register_command(
        "dream",
        _handle_slash,
        description="Consolidate recent sessions into long-term memory",
        args_hint="[run|dreams|status]",
    )

    # 4) Terminal subcommand: hermes dream ...
    try:
        ctx.register_cli_command(
            "dream",
            help="Memory consolidation (dreaming): status, run, dreams",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Episodic→declarative memory consolidation",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("dreaming: could not register CLI command: %s", exc)

    logger.debug("dreaming plugin registered")
