"""oc_orchestrator: the supervisory control plane over the agent fleet.

A bounded hierarchy of identical supervisor nodes running deterministic mechanism
code (cap enforcement, idempotent recovery, liveness/stall detection, the driver
tick) with a thin advisory brain consulted only at ambiguous decision points.

Built strictly ON the Feature B substrate (the oc_runs spine + reconciler) and
the oc_agents/oc_teams/oc_flow spawn seams, consumed by capability. Caps are
enforced at ONE choke point (caps.spawn_guarded) backed by ONE atomic
reservation ledger, so runaway fan-out is impossible by construction. Recovery is
intent-then-execute, so a crash mid-spawn neither double-spawns nor abandons a
task. See agents-mission/03-design-orchestrator.md.
"""

import logging

logger = logging.getLogger("hermes.plugins.oc_orchestrator")


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def _handle_slash(args_str: str = "") -> str:
    """In-session ``/orchestrator <goal>`` slash command: plan only (no Kanban
    side effects from a chat turn), returning the routed plan as text."""
    goal = (args_str or "").strip()
    if not goal:
        return "Usage: /orchestrator <goal> — routes the goal to specialized profiles."
    from . import brain

    profiles = ["coder", "atlas", "sage", "ledger", "finance"]
    llm = None
    try:
        llm = brain.gateway_llm()
    except Exception:
        llm = None
    plan = brain.route_decompose(goal, profiles, llm=llm, max_fanout=5)
    lines = [f"shape={plan['shape']} lead={plan['lead']}"]
    if plan["shape"] == "swarm":
        for st in plan["subtasks"]:
            lines.append(f"  - {st['profile']}: {st['subtask'][:80]}")
    return "\n".join(lines)


def register(ctx) -> None:
    """Plugin entry point: register the CLI command and the slash command."""
    try:
        ctx.register_cli_command(
            "orchestrator",
            help="Route + decompose a goal to specialized profiles; inspect ledger state",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Supervisory control plane: route, decompose, assign, recover",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_orchestrator: could not register CLI command: %s", exc)
    try:
        ctx.register_command(
            "orchestrator",
            _handle_slash,
            description="Route a goal to specialized profiles (plan only)",
            args_hint="<goal>",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_orchestrator: could not register slash command: %s", exc)
    logger.debug("oc_orchestrator plugin registered")
