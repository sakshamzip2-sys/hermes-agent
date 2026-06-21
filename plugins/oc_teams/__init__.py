"""oc_teams — Agent Teams for OpenComputer v2.

Ports Claude Code's *agent teams* concept into the v2 idiom: a lead agent and
teammates share a task list (with dependencies + atomic claiming) and a mailbox,
and coordinate through service-gated tools. Teammates run as Phase-2 background
agent sessions (``plugins.oc_agents``) with ``HERMES_TEAM_ID`` in their env.

Surfaces
--------
* ``hermes team create|spawn|tasks|task-add|task-claim|task-done|send|inbox|
  members|show|list|shutdown|cleanup`` — terminal control surface.
* ``/team`` — in-session slash command (status/tasks/spawn).
* Service-gated tools (``team_status``, ``team_claim_task``, ``team_send_message``,
  …) visible only inside a team session (``HERMES_TEAM_ID`` set), so a normal
  session's tool schema is untouched.

State lives in a standalone SQLite DB (``<root>/oc_teams.db``); the core
``hermes_state`` schema is never touched.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.oc_teams")

_SLASH_HELP = """Agent teams. Subcommands:
  /team                       list teams
  /team show <team_id>        show members + shared tasks
  /team spawn <team_id> <member> <prompt>   spawn a teammate"""


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def _handle_slash(raw_args: str):
    from . import coordinator, db

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "list"

    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP

    if sub in ("list", ""):
        teams = db.list_teams(include_cleaned=False)
        if not teams:
            return "No active teams. Create one: hermes team create \"<name>\""
        return "Active teams:\n" + "\n".join(f"  {t['id']}  {t['name']}" for t in teams)

    if sub == "show":
        if len(parts) < 2:
            return "usage: /team show <team_id>"
        s = db.team_status_summary(parts[1])
        if not s.get("team"):
            return f"No such team {parts[1]}"
        members = ", ".join(f"{m['name']}({m['status']})" for m in s["members"])
        counts = "  ".join(f"{k}={v}" for k, v in s["task_counts"].items())
        return f"Team {parts[1]}: {len(s['members'])} members [{members}]\n  tasks: {counts or 'none'}"

    if sub == "spawn":
        if len(parts) < 4:
            return "usage: /team spawn <team_id> <member> <prompt>"
        team_id, member = parts[1], parts[2]
        prompt = raw_args.split(None, 3)[3]
        if db.get_team(team_id) is None:
            return f"No such team {team_id}"
        try:
            bg = coordinator.spawn_teammate(team_id, member, prompt)
        except Exception as exc:  # noqa: BLE001
            return f"spawn failed: {exc}"
        return f"teammate '{member}' spawned (bg {bg}). Watch: oc agents show {bg}"

    return _SLASH_HELP


def register(ctx) -> None:
    # Service-gated model tools (only visible inside a team session).
    try:
        from . import tools

        tools.register_team_tools(ctx)
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_teams: could not register team tools: %s", exc)

    ctx.register_command(
        "team",
        _handle_slash,
        description="Create teams, spawn teammates, manage the shared task list",
        args_hint="[list|show <id>|spawn <id> <member> <prompt>]",
    )
    try:
        ctx.register_cli_command(
            "team",
            help="Agent teams: create/spawn teammates, shared task list + mailbox",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Lead + teammates with a shared task list and mailbox",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_teams: could not register CLI command: %s", exc)

    logger.debug("oc_teams plugin registered")
