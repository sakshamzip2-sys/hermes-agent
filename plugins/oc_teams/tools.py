"""Service-gated model tools for agent teammates.

These tools are only visible when ``HERMES_TEAM_ID`` is set in the environment
(``check_fn=_team_mode_active``) — i.e. inside a teammate's background session or
a lead session that has joined a team. A normal ``hermes`` session sees none of
them, so the core tool schema (sent on every API call) is unaffected. The acting
member is read from ``HERMES_TEAM_MEMBER``.

Each handler follows the registry contract: ``(args: dict, **kw) -> str`` where
the return value is a JSON string (or a ``tool_error``).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from tools.registry import tool_error

from . import db


def _team_mode_active() -> bool:
    return bool(os.environ.get("HERMES_TEAM_ID", "").strip())


def _ctx() -> tuple[Optional[str], str]:
    return (os.environ.get("HERMES_TEAM_ID", "").strip() or None,
            os.environ.get("HERMES_TEAM_MEMBER", "").strip() or "lead")


def _ok(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


def _fire_team_hook(name: str, **kwargs: Any) -> Optional[str]:
    """Fire a team lifecycle hook; return a veto message if any callback blocks.

    Mirrors v2's canonical block contract so team gates behave like every other
    blocking hook: a callback returns ``{"action": "block", "message": "..."}``
    (canonical) or ``{"decision": "block", "reason": "..."}`` (Claude-Code shape)
    to veto, or ``None`` to observe. The first veto wins.

    Fails OPEN: if the plugin system is unavailable or no callback vetoes, this
    returns ``None`` and the caller proceeds unchanged — a normal team session
    with no quality-gate plugins behaves exactly as before.
    """
    try:
        from hermes_cli.plugins import invoke_hook
    except Exception:  # noqa: BLE001 — never let hook plumbing break a team tool
        return None
    try:
        results = invoke_hook(name, **kwargs)
    except Exception:  # noqa: BLE001
        return None
    for r in results:
        if not isinstance(r, dict):
            continue
        if r.get("action") == "block":
            msg = r.get("message")
            return msg if isinstance(msg, str) and msg else "blocked"
        if r.get("decision") == "block":
            reason = r.get("reason")
            return reason if isinstance(reason, str) and reason else "blocked"
    return None


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

def _handle_status(args: dict, **kw) -> str:
    team_id, _member = _ctx()
    if not team_id:
        return tool_error("not in a team (HERMES_TEAM_ID unset)")
    return _ok(db.team_status_summary(team_id))


def _handle_list_tasks(args: dict, **kw) -> str:
    team_id, _member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    status = args.get("status")
    if status == "claimable":
        return _ok({"tasks": db.claimable_tasks(team_id)})
    return _ok({"tasks": db.list_tasks(team_id, status=status)})


def _handle_create_task(args: dict, **kw) -> str:
    team_id, member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    subject = (args.get("subject") or "").strip()
    if not subject:
        return tool_error("subject is required")
    description = args.get("description", "") or ""
    block = _fire_team_hook(
        "team_task_created", team_id=team_id, subject=subject,
        description=description, created_by=member,
    )
    if block:
        return tool_error(f"task creation blocked: {block}")
    depends_on = args.get("depends_on")
    if isinstance(depends_on, str):
        depends_on = [d.strip() for d in depends_on.split(",") if d.strip()]
    tid = db.create_task(
        team_id, subject, description=description,
        depends_on=depends_on if isinstance(depends_on, list) else None,
        created_by=member,
    )
    return _ok({"task_id": tid, "status": "pending"})


def _handle_claim_task(args: dict, **kw) -> str:
    team_id, member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return tool_error("task_id is required")
    won = db.claim_task(task_id, member)
    if not won:
        t = db.get_task(task_id)
        if t is None:
            return tool_error(f"no such task {task_id}")
        return _ok({"claimed": False, "reason": f"task is {t['status']} owned by '{t['owner']}'"})
    return _ok({"claimed": True, "task_id": task_id, "owner": member})


def _handle_complete_task(args: dict, **kw) -> str:
    team_id, member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    task_id = (args.get("task_id") or "").strip()
    if not task_id:
        return tool_error("task_id is required")
    result = args.get("result", "")
    # Quality gate: a registered hook may veto completion (e.g. require evidence).
    task = db.get_task(task_id)
    block = _fire_team_hook(
        "team_task_completed", team_id=team_id, task_id=task_id, member=member,
        result=result, subject=(task or {}).get("subject", ""),
    )
    if block:
        return _ok({"completed": False, "blocked": True, "reason": block, "task_id": task_id})
    if db.complete_task(task_id, member):
        if result:
            db.send_message(team_id, member, "lead", f"[task {task_id} done] {result}")
        # Report which dependent tasks are now unblocked.
        unblocked = [t["id"] for t in db.claimable_tasks(team_id)]
        payload: Dict[str, Any] = {"completed": True, "task_id": task_id, "now_claimable": unblocked}
        # If the teammate has nothing left to claim it is about to go idle —
        # let a hook nudge it to keep working instead of stopping.
        if not unblocked:
            nudge = _fire_team_hook("team_teammate_idle", team_id=team_id, member=member)
            if nudge:
                payload["idle_nudge"] = nudge
        return _ok(payload)
    return tool_error(f"could not complete {task_id} (already completed or missing)")


def _handle_send_message(args: dict, **kw) -> str:
    team_id, member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    to = (args.get("to") or "").strip()
    body = (args.get("body") or "").strip()
    if not to or not body:
        return tool_error("'to' and 'body' are required ('to' may be a member name or '*' for all)")
    mid = db.send_message(team_id, member, to, body)
    return _ok({"sent": True, "message_id": mid, "to": to})


def _handle_read_inbox(args: dict, **kw) -> str:
    team_id, member = _ctx()
    if not team_id:
        return tool_error("not in a team")
    msgs = db.read_inbox(team_id, member, mark_read=True)
    return _ok({"messages": [{"from": m["from_member"], "to": m["to_member"], "body": m["body"]} for m in msgs]})


# --------------------------------------------------------------------------- #
# Schemas + registration
# --------------------------------------------------------------------------- #

def _obj(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_TOOLS = [
    ("team_status", _handle_status, "Show team members, tasks, and status counts.",
     _obj({}), "👥"),
    ("team_list_tasks", _handle_list_tasks,
     "List the team's shared tasks. Pass status='claimable' for unblocked, unowned tasks.",
     _obj({"status": {"type": "string", "description": "pending|in_progress|completed|claimable"}}), "📋"),
    ("team_create_task", _handle_create_task,
     "Add a task to the shared list (optionally depending on other task ids).",
     _obj({"subject": {"type": "string"}, "description": {"type": "string"},
           "depends_on": {"type": "array", "items": {"type": "string"}}}, ["subject"]), "➕"),
    ("team_claim_task", _handle_claim_task,
     "Atomically claim a task. Only one teammate can win a given task.",
     _obj({"task_id": {"type": "string"}}, ["task_id"]), "✋"),
    ("team_complete_task", _handle_complete_task,
     "Mark your claimed task done (unblocks dependents); include a short result.",
     _obj({"task_id": {"type": "string"}, "result": {"type": "string"}}, ["task_id"]), "✅"),
    ("team_send_message", _handle_send_message,
     "Message a teammate by name, or '*' to broadcast to the whole team.",
     _obj({"to": {"type": "string"}, "body": {"type": "string"}}, ["to", "body"]), "✉️"),
    ("team_read_inbox", _handle_read_inbox,
     "Read (and clear) messages addressed to you or broadcast to the team.",
     _obj({}), "📥"),
]


def register_team_tools(ctx) -> None:
    """Register all team tools, gated so they only appear inside a team session."""
    for name, handler, desc, schema, emoji in _TOOLS:
        try:
            ctx.register_tool(
                name=name, toolset="team", schema=schema, handler=handler,
                check_fn=_team_mode_active, description=desc, emoji=emoji,
            )
        except Exception:  # noqa: BLE001 — never fail plugin load on one tool
            pass
