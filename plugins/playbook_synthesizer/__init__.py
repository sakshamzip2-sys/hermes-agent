"""playbook_synthesizer — DREAM→EVOLVE: turn recurring patterns into reusable skills.

The loop-closer. The agent's dreaming/outcomes surface recurring multi-step patterns;
this plugin synthesizes them into durable SKILL.md files (via the existing
``skill_manager_tool``), so future turns are guided by what the agent already learned to
do well. Autonomous-but-safe: skills are agent-created (curator-managed) and versioned;
default-OFF until explicitly enabled.

Edge plugin — no core tool. Exposes ``/playbook`` slash + ``hermes playbook`` CLI + a
``run()`` entrypoint the nightly self-evolution cycle calls after dreaming.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.playbook_synthesizer")

_SLASH_HELP = """Playbook synthesizer — turn recurring patterns into skills. Subcommands:
  /playbook            show status (enabled, recent synthesis)
  /playbook run        synthesize from supplied/recent candidates now"""


# --- skill-system adapters (the real creator_fn / exists_fn) ----------------
def _default_creator_fn(*, name: str, content: str, category=None):  # noqa: ANN001
    """Create a skill via the existing skill_manager tool (agent-created + versioned)."""
    from tools.skill_manager_tool import skill_manage

    return skill_manage(action="create", name=name, content=content, category=category)


def _default_exists_fn(slug: str) -> bool:
    """True if a skill named ``slug`` already exists anywhere on the skills path."""
    try:
        from agent.skill_utils import get_all_skills_dirs

        for skills_dir in get_all_skills_dirs():
            if (skills_dir / slug / "SKILL.md").exists():
                return True
    except Exception as exc:  # noqa: BLE001 — fail toward "exists" to avoid clobbering
        logger.debug("playbook_synthesizer: exists check failed (%s); assuming exists", exc)
        return True
    return False


def run(candidates=None, *, creator_fn=None, exists_fn=None, config=None) -> dict:  # noqa: ANN001
    """Synthesize skills from ``candidates`` (a list of PlaybookCandidate).

    The nightly self-evolution cycle passes candidates extracted from recent dreaming
    output. Returns a summary. No-op (created=[]) when disabled or no candidates.
    """
    from .config import load_playbook_config
    from .synthesizer import synthesize

    cfg = config or load_playbook_config()
    summary = {"enabled": cfg.enabled, "created": [], "skipped": []}
    if not cfg.enabled:
        summary["reason"] = "disabled"
        return summary
    if not candidates:
        summary["reason"] = "no_candidates"
        return summary

    creator = creator_fn or _default_creator_fn
    exists = exists_fn or _default_exists_fn
    made = 0
    for cand in candidates:
        if made >= cfg.max_per_cycle:
            summary["skipped"].append({"name": getattr(cand, "name", "?"), "reason": "cap"})
            continue
        res = synthesize(cand, creator_fn=creator, exists_fn=exists, category=cfg.category)
        if res.get("created"):
            made += 1
            summary["created"].append(res["name"])
        else:
            summary["skipped"].append({"name": res.get("name"), "reason": res.get("reason")})
    return summary


async def synthesize_from_facts(
    facts, *, chat_fn=None, creator_fn=None, exists_fn=None, config=None,  # noqa: ANN001
) -> dict:
    """DREAM→EVOLVE bridge: extract playbook candidates from recent dreaming facts and
    synthesize skills. This is what the nightly cycle calls right after a dream run.

    Fail-soft + default-OFF: returns a disabled/empty summary when not enabled or when the
    extractor finds nothing.
    """
    from .config import load_playbook_config
    from .extractor import extract_candidates

    cfg = config or load_playbook_config()
    if not cfg.enabled:
        return {"enabled": False, "created": [], "skipped": [], "reason": "disabled"}
    candidates = await extract_candidates(list(facts or []), chat_fn=chat_fn)
    return run(candidates, creator_fn=creator_fn, exists_fn=exists_fn, config=cfg)


# --- slash + CLI ------------------------------------------------------------
def _render_status() -> str:
    from .config import load_playbook_config

    cfg = load_playbook_config()
    return (
        "Playbook synthesizer (DREAM→EVOLVE)\n"
        f"  enabled: {cfg.enabled}   max/cycle: {cfg.max_per_cycle}   "
        f"category: {cfg.category}"
    )


def _handle_slash(raw_args: str):
    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "status"
    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP
    if sub == "run":
        # No candidate source on the bare slash path — the nightly cycle supplies them.
        out = run(candidates=None)
        return f"Playbook run: {out.get('reason', 'ok')} (created={len(out.get('created', []))})"
    return _render_status()


def _cli_setup(subparser) -> None:
    import argparse

    sub = subparser.add_subparsers(dest="playbook_cmd")
    p_status = sub.add_parser("status", help="show synthesizer config")
    p_status.set_defaults(func=lambda a: (print(_render_status()) or 0))
    subparser.set_defaults(func=lambda a: (print(_render_status()) or 0))
    _ = argparse  # keep import referenced


def _cli_handle(args) -> int:
    func = getattr(args, "func", None)
    if func is None:
        print(_render_status())
        return 0
    return func(args)


def register(ctx) -> None:
    """Plugin entry point — slash + CLI + aux task (for pattern extraction)."""
    try:
        ctx.register_auxiliary_task(
            key="playbook_synthesizer",
            display_name="Playbook synthesis",
            description="Distils recurring patterns into reusable skills",
            defaults={"provider": "auto", "timeout": 30},
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("playbook_synthesizer: aux task registration failed (%s)", exc)

    ctx.register_command(
        "playbook",
        _handle_slash,
        description="Synthesize recurring patterns into skills (DREAM→EVOLVE)",
        args_hint="[status|run]",
    )
    try:
        ctx.register_cli_command(
            "playbook",
            help="Playbook synthesizer: status, run",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="EVOLVE — synthesize skills from recurring patterns",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("playbook_synthesizer: CLI registration failed (%s)", exc)

    logger.debug("playbook_synthesizer plugin registered")
