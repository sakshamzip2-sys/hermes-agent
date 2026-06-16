"""Team coordination: create teams, spawn teammates, shut them down, clean up.

A teammate is a Phase-2 background agent session (``plugins.oc_agents``) launched
with ``HERMES_TEAM_ID`` / ``HERMES_TEAM_MEMBER`` in its environment. That env both
scopes the teammate's team-tool calls and triggers the ``check_fn`` that makes the
service-gated team tools visible in its session — so a normal session never pays
for them. The teammate's prompt is augmented with the coordination protocol so it
knows to claim tasks and message the team.
"""

from __future__ import annotations

import logging

from . import db

logger = logging.getLogger("hermes.plugins.oc_teams.coordinator")

PROTOCOL_PREAMBLE = """\
You are **{member}** (role: {role}) on agent team "{team_name}" (id: {team_id}).
Team goal: {goal}

You coordinate with teammates through a shared task list and a mailbox. Use these
tools (available in your session):
  • team_status()                       — see members, tasks, and counts
  • team_list_tasks(status=...)         — list tasks (claimable ones have no owner and met deps)
  • team_claim_task(task_id)            — atomically claim a task (only one teammate wins)
  • team_complete_task(task_id, result) — mark your task done (unblocks dependents)
  • team_create_task(subject, ...)      — add a task to the shared list
  • team_send_message(to, body)         — message a teammate by name, or "*" for all
  • team_read_inbox()                   — read (and clear) messages addressed to you

Protocol:
  1. Read your inbox and team_status first.
  2. Claim an unblocked task you can do, do it, then team_complete_task with a short result.
  3. If you finish and there's no claimable task, message the lead and stop.
  4. Keep messages short; put durable findings in task results.

Your assignment:
{prompt}
"""


def create_team(name: str, goal: str = "", lead_name: str = "lead") -> str:
    team_id = db.new_team_id()
    db.create_team(team_id, name, goal, lead_name=lead_name)
    logger.debug("oc_teams: created team %s (%s)", team_id, name)
    return team_id


def _augment_prompt(team_id: str, member: str, role: str, prompt: str, *, persona: str = "") -> str:
    team = db.get_team(team_id) or {}
    preamble = PROTOCOL_PREAMBLE.format(
        member=member, role=role or "teammate",
        team_name=team.get("name", ""), team_id=team_id,
        goal=team.get("goal", "") or "(see tasks)", prompt=prompt,
    )
    # An agent-type definition's body becomes the teammate's persona, prepended
    # to the team-coordination protocol so role expertise + team rules coexist.
    return (persona.strip() + "\n\n" + preamble) if persona.strip() else preamble


def spawn_teammate(
    team_id: str, name: str, prompt: str, *, role: str = "", model: str = "",
    cwd: str = "", agent_type: str = "", permission_mode: str = "", dispatch_fn=None,
) -> str:
    """Register a teammate and launch its background session. Returns the bg session id.

    When ``agent_type`` names a reusable agent definition (see
    ``tools.agent_defs``), its persona/toolsets/model/provider seed the teammate;
    explicit ``role``/``model`` args still win. An unknown ``agent_type`` raises
    *before* the member is registered, so a typo never half-creates a teammate.

    ``dispatch_fn`` is injected in tests; by default it is the oc_agents supervisor.
    """
    if db.get_team(team_id) is None:
        raise ValueError(f"unknown team {team_id}")

    persona = ""
    toolsets = None
    provider = ""
    definition = None
    if agent_type:
        from tools.agent_defs import get_agent_definition

        definition = get_agent_definition(agent_type)
        if definition is None:
            raise ValueError(f"unknown agent type '{agent_type}'")
        role = role or definition.name
        model = model or (definition.model or "")
        provider = definition.provider or ""
        toolsets = definition.toolsets
        persona = definition.prompt
        permission_mode = permission_mode or (definition.permission_mode or "")

    if not db.add_member(team_id, name, role=role, kind=db.MEMBER_TEAMMATE):
        raise ValueError(f"member name '{name}' already exists on team {team_id}")

    full_prompt = _augment_prompt(team_id, name, role, prompt, persona=persona)
    extra_env = {"HERMES_TEAM_ID": team_id, "HERMES_TEAM_MEMBER": name}
    # Forward a permission mode so the teammate's process starts e.g. read-only
    # in plan mode (honored at worker startup via _apply_startup_permission_mode).
    if permission_mode:
        extra_env["HERMES_PERMISSION_MODE"] = permission_mode
    # Forward a scoped persistent-memory dir so an agent-type with a memory:
    # scope accumulates learnings across runs (honored via get_memory_dir).
    if definition is not None:
        from tools.agent_defs import resolve_memory_dir

        _mem = resolve_memory_dir(definition, cwd=cwd or None)
        if _mem is not None:
            extra_env["HERMES_MEMORY_DIR"] = str(_mem)

    if dispatch_fn is None:
        from plugins.oc_agents import supervisor

        dispatch_fn = supervisor.dispatch

    bg_id = dispatch_fn(
        full_prompt, name=f"{team_id}:{name}", cwd=cwd, model=model,
        provider=provider, toolsets=toolsets, kind="teammate",
        parent_id=team_id, extra_env=extra_env,
    )
    db.set_member_session(team_id, name, bg_id)
    logger.debug("oc_teams: spawned teammate %s on team %s (bg=%s)", name, team_id, bg_id)
    return bg_id


def shutdown_teammate(team_id: str, name: str, *, stop_fn=None) -> bool:
    member = db.get_member(team_id, name)
    if member is None or member["kind"] != db.MEMBER_TEAMMATE:
        return False
    bg_id = member.get("bg_session_id")
    if bg_id:
        if stop_fn is None:
            from plugins.oc_agents import supervisor

            stop_fn = supervisor.stop
        try:
            stop_fn(bg_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("oc_teams: stop teammate %s failed: %s", name, exc)
    db.set_member_status(team_id, name, "shutdown")
    return True


def cleanup_team(team_id: str, *, force: bool = False, stop_fn=None) -> bool:
    """Clean up a team. Without ``force`` it refuses while teammates are active."""
    if db.get_team(team_id) is None:
        return False
    active = db.active_teammates(team_id)
    if active and not force:
        raise RuntimeError(
            f"{len(active)} teammate(s) still active: {', '.join(m['name'] for m in active)}. "
            "Shut them down first, or cleanup with force=True."
        )
    if active and force:
        for m in active:
            shutdown_teammate(team_id, m["name"], stop_fn=stop_fn)
    db.set_team_status(team_id, "cleaned")
    return True
