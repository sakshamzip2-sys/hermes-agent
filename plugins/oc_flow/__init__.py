"""oc_flow — Dynamic Workflows for OpenComputer v2.

Ports Claude Code's *dynamic workflows* concept into the v2 idiom (a plugin at
the edge — no new core tool). A *flow* is a trusted, locally-authored Python
script that orchestrates many real v2 subagents at scale: it holds the plan
(loops, branches, fan-out) in code, keeps intermediate results in script
variables, runs in the background, and is resumable.

Surfaces
--------
* ``oc flow run|list|show|logs|stop|examples`` — terminal command.
* ``/flow`` — in-session slash command (list/show/run).

Persistence lives in a standalone SQLite DB (``<root>/oc_flow.db``) so runs are
observable across processes and resumable across invocations. The core
``hermes_state`` schema is never touched.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.oc_flow")

_SLASH_HELP = """Dynamic workflows. Subcommands:
  /flow                 list recent runs
  /flow list            list recent runs
  /flow show <run_id>   show a run's phases, agents, and result
  /flow run <script>    run a flow script (foreground)"""


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def _handle_slash(raw_args: str):
    """In-session ``/flow`` handler: ``fn(raw_args: str) -> str``."""
    from . import db

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "list"

    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP

    if sub in ("list", ""):
        runs = db.list_runs(limit=15)
        if not runs:
            return "No flow runs yet. Run one with: oc flow run <script.py>"
        lines = ["Recent flows:"]
        for r in runs:
            lines.append(f"  {r['id']}  {r['status']:<10} {r['agent_count']:>3} agents  {r['name']}")
        return "\n".join(lines)

    if sub == "show":
        if len(parts) < 2:
            return "usage: /flow show <run_id>"
        run = db.get_run(parts[1])
        if not run:
            return f"No such run {parts[1]}"
        phases = db.list_phases(parts[1])
        agents = db.list_agents(parts[1])
        lines = [
            f"Flow {run['id']} — {run['name']}",
            f"  status: {run['status']}   agents: {len(agents)}   phases: {len(phases)}",
        ]
        if run.get("error"):
            lines.append(f"  error: {run['error']}")
        return "\n".join(lines)

    if sub == "run":
        if len(parts) < 2:
            return "usage: /flow run <script.py>"
        from pathlib import Path

        from .runtime import run_flow

        script = parts[1]
        if not Path(script).is_file():
            return f"flow: script not found: {script}"
        outcome = run_flow(script_path=script)
        if outcome.status == "completed":
            return f"flow {outcome.run_id}: completed ({outcome.agent_count} agents). See: oc flow show {outcome.run_id}"
        return f"flow {outcome.run_id}: {outcome.status} — {outcome.error or ''}"

    return _SLASH_HELP


def register(ctx) -> None:
    """Plugin entry point."""
    ctx.register_command(
        "flow",
        _handle_slash,
        description="Run and inspect dynamic workflows (subagent orchestration)",
        args_hint="[list|show <id>|run <script>]",
    )
    try:
        ctx.register_cli_command(
            "flow",
            help="Dynamic workflows: run/list/show/logs/stop subagent orchestration scripts",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Orchestrate many subagents from a resumable script",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_flow: could not register CLI command: %s", exc)

    logger.debug("oc_flow plugin registered")
