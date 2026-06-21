"""oc_agents — Agent View for OpenComputer v2.

Ports Claude Code's *agent view* concept into the v2 idiom (a plugin at the
edge — no new core tool). Dispatch many headless agent sessions to run detached,
watch their state from one place (working / needs-input / done / failed), follow
one live, and stop it — without a long-lived supervisor daemon.

Each session is a detached worker process (``oc agents _worker``) that
builds a headless ``AIAgent`` like ``hermes -z`` and self-reports into a
standalone SQLite registry (``<root>/oc_agents.db``). Liveness is reconciled on
every read, so a crashed worker shows as ``failed`` rather than a ghost.

Surfaces
--------
* ``oc agents dispatch|list|show|logs|attach|stop|rm|pin`` — terminal.
* ``/agents`` — in-session slash command (list/show).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.oc_agents")

_SLASH_HELP = """Background agent sessions (agent view). Subcommands:
  /bgagents                list background sessions
  /bgagents list           list background sessions
  /bgagents show <id>      show a session's details
  /bgagents dispatch <task>  start a background agent session"""


def _cli_setup(subparser) -> None:
    from . import cli

    cli.setup(subparser)


def _cli_handle(args) -> int:
    from . import cli

    return cli.handle(args)


def _handle_slash(raw_args: str):
    from . import db, supervisor

    parts = (raw_args or "").split()
    sub = parts[0].lower() if parts else "list"

    if sub in ("help", "-h", "--help"):
        return _SLASH_HELP

    if sub in ("list", ""):
        sessions = supervisor.snapshot(include_done=False)
        if not sessions:
            return "No active background sessions. Start one: oc agents dispatch \"<task>\""
        lines = ["Active background sessions:"]
        for s in sessions:
            lines.append(f"  {s['id']}  {s['status']:<12} {s['name']}")
            if s.get("last_summary"):
                lines.append(f"     {s['last_summary'][:80]}")
        return "\n".join(lines)

    if sub == "show":
        if len(parts) < 2:
            return "usage: /agents show <id>"
        s = db.get_session(parts[1])
        if not s:
            return f"No such session {parts[1]}"
        out = [f"Session {s['id']} — {s['name']} [{s['status']}]", f"  prompt: {s['prompt'][:160]}"]
        if s.get("last_summary"):
            out.append(f"  latest: {s['last_summary'][:160]}")
        if s.get("result"):
            out.append(f"  result: {str(s['result'])[:300]}")
        return "\n".join(out)

    if sub == "dispatch":
        if len(parts) < 2:
            return "usage: /agents dispatch <task>"
        task = raw_args.split(None, 1)[1]
        sid = supervisor.dispatch(task)
        return f"agent {sid}: dispatched in background. Watch: oc agents show {sid}"

    return _SLASH_HELP


def register(ctx) -> None:
    ctx.register_command(
        "bgagents",
        _handle_slash,
        description="Dispatch and manage background agent sessions (agent view)",
        args_hint="[list|show <id>|dispatch <task>]",
    )
    try:
        ctx.register_cli_command(
            "agents",
            help="Background agent sessions: dispatch/list/show/logs/attach/stop",
            setup_fn=_cli_setup,
            handler_fn=_cli_handle,
            description="Dispatch & manage many background agent sessions from one place",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_agents: could not register CLI command: %s", exc)

    logger.debug("oc_agents plugin registered")
