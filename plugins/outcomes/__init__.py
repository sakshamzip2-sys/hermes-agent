"""outcomes — per-turn quality scoring (the SENSE organ of the self-evolution loop).

Synthesizes a per-turn ``turn_score`` in [0, 1] from implicit conversation signals
(tool success/error, corrections, abandonment) plus an optional auxiliary-LLM judge.
The score is the missing molecule that lets the rest of the loop get smarter:

* dreaming reads recent turn_scores to tune its promotion threshold (be stricter when
  outcomes dip, more permissive when they recover);
* background-review / playbook synthesis can prioritise the turns that went badly or
  that contain a reusable win;
* the cross-engine plane (Honcho/GBrain) reads it to mark which sessions mattered.

Ported from OpenComputer v1's ``evolution`` stack (composite_scorer + judge_reviewer +
score_fusion + dreaming_outcomes) into the v2 idiom as a self-contained edge plugin —
no new core tool; wired through the existing public hooks
(``post_tool_call`` / ``post_llm_call`` / ``on_session_end``).

Default-OFF. Composite scoring is free (pure arithmetic, no LLM); the judge is opt-in.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.outcomes")

_ENGINE = None  # lazy singleton


def _get_engine():
    """Lazily build the shared OutcomesEngine bound to the on-disk store + config."""
    global _ENGINE
    if _ENGINE is None:
        from .config import load_outcomes_config
        from .engine import OutcomesEngine
        from .store import OutcomesStore, default_db_path

        cfg = load_outcomes_config()
        _ENGINE = OutcomesEngine(OutcomesStore(default_db_path()), judge_enabled=cfg.judge_enabled)
    return _ENGINE


_ENABLED_CACHE: tuple[bool, float] = (False, 0.0)
_ENABLED_TTL = 30.0  # seconds — avoid a config-file read on every per-tool-call hook


def _enabled() -> bool:
    """Cached read of ``outcomes.enabled`` (post_tool_call fires per tool call, so a
    fresh config read each time would hammer the disk on the hot path)."""
    global _ENABLED_CACHE
    import time as _t

    val, ts = _ENABLED_CACHE
    now = _t.monotonic()
    if now - ts < _ENABLED_TTL:
        return val
    try:
        from .config import load_outcomes_config

        val = load_outcomes_config().enabled
    except Exception:  # noqa: BLE001
        val = False
    _ENABLED_CACHE = (val, now)
    return val


# --- slash + CLI rendering --------------------------------------------------
_SLASH_HELP = """Outcomes — per-turn quality scoring (SENSE). Subcommands:
  /outcomes          show status (recent mean turn_score, count)
  /outcomes run      emit a cycle summary now"""


def _render_status() -> str:
    try:
        from .config import load_outcomes_config

        cfg = load_outcomes_config()
        eng = _get_engine()
        summary = eng.run_cycle()
        mean = summary.get("mean_recent")
        mean_s = f"{mean:.3f}" if isinstance(mean, (int, float)) else "n/a"
        return (
            "Outcomes (per-turn scoring)\n"
            f"  enabled: {cfg.enabled}   judge: {cfg.judge_enabled}\n"
            f"  recorded turns: {summary.get('recorded', 0)}   "
            f"recent mean turn_score: {mean_s}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"outcomes status failed: {type(exc).__name__}: {exc}"


def _handle_slash(raw_args: str):
    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"
    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP
    if sub == "run":
        try:
            summary = _get_engine().run_cycle()
            return (
                f"Outcomes cycle: recorded={summary.get('recorded', 0)} "
                f"recent_n={summary.get('recent_n', 0)} "
                f"mean_recent={summary.get('mean_recent')}"
            )
        except Exception as exc:  # noqa: BLE001
            return f"outcomes run failed: {type(exc).__name__}: {exc}"
    return _render_status()


def run_cycle() -> dict:
    """Nightly-deployment entrypoint (the seam Session-B's deployment sequences first).

    Runs the lightweight rollup, then — when the judge is enabled — re-scores recent
    composite-only turns with the aux-LLM judge in one bounded batch pass (the judge is
    async + costs tokens, so it lives HERE, not on the per-turn hot path).
    """
    eng = _get_engine()
    summary = eng.run_cycle()
    rejudged = 0
    try:
        from .config import load_outcomes_config

        if load_outcomes_config().judge_enabled:
            import asyncio

            standing = load_outcomes_config().standing_orders
            rejudged = asyncio.run(eng.rejudge_recent(standing_orders=standing))
    except RuntimeError as exc:  # already inside an event loop — skip (hook path is sync)
        logger.debug("outcomes: rejudge skipped, running loop (%s)", exc)
    except Exception as exc:  # noqa: BLE001 — judge must never break the cycle
        logger.debug("outcomes: rejudge failed (%s)", exc)
    summary["rejudged"] = rejudged
    return summary


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def register(ctx) -> None:
    """Plugin entry point — wire the auxiliary judge task, hooks, and commands.

    Default-OFF: when ``outcomes.enabled`` is false the hooks are still registered but
    short-circuit, so there is zero per-turn cost until the user opts in.
    """
    # 1) Auxiliary task so users can pin a cheap judge model (opt-in).
    try:
        ctx.register_auxiliary_task(
            key="outcomes",
            display_name="Outcomes judge",
            description="Scores per-turn quality for the self-evolution loop",
            defaults={"provider": "auto", "timeout": 20},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: could not register auxiliary task: %s", exc)

    # 2) Hooks — gated on enabled() so disabled = no-op.
    from . import hooks as _hooks

    def _guard(cb):
        def _wrapped(**kwargs):  # noqa: ANN003
            if not _enabled():
                return None
            try:
                return cb(**kwargs)
            except Exception as exc:  # noqa: BLE001 — fail-open
                logger.debug("outcomes: hook failed (%s)", exc)
                return None

        return _wrapped

    try:
        eng = _get_engine()
        ctx.register_hook("post_tool_call", _guard(_hooks.make_post_tool_call(eng)))
        ctx.register_hook("post_llm_call", _guard(_hooks.make_post_llm_call(eng)))
        ctx.register_hook("on_session_end", _guard(_hooks.make_on_session_end(eng)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: could not register hooks: %s", exc)

    # 3) Slash command.
    ctx.register_command(
        "outcomes",
        _handle_slash,
        description="Per-turn quality scoring (SENSE organ of the self-evolution loop)",
        args_hint="[status|run]",
    )

    # 4) Terminal subcommand.
    try:
        ctx.register_cli_command(
            "outcomes",
            help="Per-turn outcome scoring: status, run",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="SENSE — per-turn turn_score for self-evolution",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: could not register CLI command: %s", exc)

    logger.debug("outcomes plugin registered")
