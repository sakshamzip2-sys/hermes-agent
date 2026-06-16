"""``hermes team`` — create teams, spawn teammates, manage the shared task list.

    hermes team create "<name>" [--goal G]
    hermes team list
    hermes team show <team_id>
    hermes team spawn <team_id> <member> "<prompt>" [--role R] [--model M]
    hermes team members <team_id>
    hermes team tasks <team_id> [--status pending|in_progress|completed|claimable]
    hermes team task-add <team_id> "<subject>" [--desc D] [--depends t1,t2]
    hermes team task-claim <team_id> <task_id> <member>
    hermes team task-done <team_id> <task_id>
    hermes team send <team_id> <from> <to> "<body>"
    hermes team inbox <team_id> <member>
    hermes team shutdown <team_id> <member>
    hermes team cleanup <team_id> [--force]
"""

from __future__ import annotations

import json
import sys
from typing import List, Optional

from . import coordinator, db


def setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="team_command", metavar="<command>")

    pc = sub.add_parser("create", help="Create a team")
    pc.add_argument("name")
    pc.add_argument("--goal", default="")

    pl = sub.add_parser("list", help="List teams")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="Show a team's members and tasks")
    ps.add_argument("team_id")
    ps.add_argument("--json", action="store_true")

    psp = sub.add_parser("spawn", help="Spawn a teammate (background session)")
    psp.add_argument("team_id")
    psp.add_argument("member")
    psp.add_argument("prompt")
    psp.add_argument("--role", default="")
    psp.add_argument("--model", default="")
    psp.add_argument("--cwd", default="")

    pm = sub.add_parser("members", help="List team members")
    pm.add_argument("team_id")

    pt = sub.add_parser("tasks", help="List the shared task list")
    pt.add_argument("team_id")
    pt.add_argument("--status", default=None)

    pta = sub.add_parser("task-add", help="Add a task")
    pta.add_argument("team_id")
    pta.add_argument("subject")
    pta.add_argument("--desc", default="")
    pta.add_argument("--depends", default="", help="Comma-separated task ids this depends on")

    ptc = sub.add_parser("task-claim", help="Claim a task for a member")
    ptc.add_argument("team_id")
    ptc.add_argument("task_id")
    ptc.add_argument("member")

    ptd = sub.add_parser("task-done", help="Mark a task completed")
    ptd.add_argument("team_id")
    ptd.add_argument("task_id")

    psnd = sub.add_parser("send", help="Send a mailbox message")
    psnd.add_argument("team_id")
    psnd.add_argument("from_member")
    psnd.add_argument("to_member")
    psnd.add_argument("body")

    pin = sub.add_parser("inbox", help="Read a member's inbox")
    pin.add_argument("team_id")
    pin.add_argument("member")
    pin.add_argument("--keep", action="store_true", help="Do not mark read")

    psd = sub.add_parser("shutdown", help="Shut down a teammate")
    psd.add_argument("team_id")
    psd.add_argument("member")

    pcl = sub.add_parser("cleanup", help="Clean up a team")
    pcl.add_argument("team_id")
    pcl.add_argument("--force", action="store_true")


def handle(args) -> int:
    cmd = getattr(args, "team_command", None)
    table = {
        "create": _create, "list": _list, "show": _show, "spawn": _spawn,
        "members": _members, "tasks": _tasks, "task-add": _task_add,
        "task-claim": _task_claim, "task-done": _task_done, "send": _send,
        "inbox": _inbox, "shutdown": _shutdown, "cleanup": _cleanup,
    }
    fn = table.get(cmd or "")
    if fn is None:
        print("usage: hermes team {create|list|show|spawn|members|tasks|task-add|"
              "task-claim|task-done|send|inbox|shutdown|cleanup} ...", file=sys.stderr)
        return 2
    return fn(args)


def _require_team(team_id: str):
    t = db.get_team(team_id)
    if t is None:
        print(f"team: no such team {team_id}", file=sys.stderr)
    return t


def _create(args) -> int:
    tid = coordinator.create_team(args.name, goal=args.goal)
    print(f"team {tid}: created  ({args.name})")
    print(f"  spawn a teammate: hermes team spawn {tid} <member> \"<prompt>\"")
    return 0


def _list(args) -> int:
    teams = db.list_teams()
    if getattr(args, "json", False):
        print(json.dumps(teams, indent=2, default=str))
        return 0
    if not teams:
        print("No teams. Create one: hermes team create \"<name>\"")
        return 0
    print(f"{'TEAM ID':<18} {'STATUS':<9} {'NAME'}")
    for t in teams:
        print(f"{t['id']:<18} {t['status']:<9} {t['name']}")
    return 0


def _show(args) -> int:
    if not _require_team(args.team_id):
        return 2
    summary = db.team_status_summary(args.team_id)
    if getattr(args, "json", False):
        print(json.dumps(summary, indent=2, default=str))
        return 0
    t = summary["team"]
    print(f"Team {t['id']} — {t['name']}   [{t['status']}]")
    if t.get("goal"):
        print(f"  goal: {t['goal']}")
    print(f"\n  Members ({len(summary['members'])}):")
    for m in summary["members"]:
        bg = f" bg={m['bg_session_id']}" if m.get("bg_session_id") else ""
        print(f"    {m['name']:<14} {m['kind']:<9} {m['status']:<9}{bg}")
    counts = summary["task_counts"]
    print(f"\n  Tasks ({summary['tasks_total']}): " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    for task in db.list_tasks(args.team_id):
        owner = f" @{task['owner']}" if task["owner"] else ""
        print(f"    {task['id']}  {task['status']:<12}{owner}  {task['subject']}")
    return 0


def _spawn(args) -> int:
    if not _require_team(args.team_id):
        return 2
    try:
        bg = coordinator.spawn_teammate(
            args.team_id, args.member, args.prompt,
            role=args.role, model=args.model, cwd=args.cwd,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"team: spawn failed: {exc}", file=sys.stderr)
        return 1
    print(f"teammate '{args.member}' spawned on {args.team_id} (bg session {bg})")
    print(f"  watch: hermes agents show {bg}")
    return 0


def _members(args) -> int:
    if not _require_team(args.team_id):
        return 2
    for m in db.list_members(args.team_id):
        print(f"  {m['name']:<14} {m['kind']:<9} {m['status']:<9} {m.get('role') or ''}")
    return 0


def _tasks(args) -> int:
    if not _require_team(args.team_id):
        return 2
    if args.status == "claimable":
        tasks = db.claimable_tasks(args.team_id)
    else:
        tasks = db.list_tasks(args.team_id, status=args.status)
    if not tasks:
        print("(no tasks)")
        return 0
    for t in tasks:
        owner = f" @{t['owner']}" if t["owner"] else ""
        deps = ""
        if t.get("depends_on"):
            deps = f"  deps={t['depends_on']}"
        print(f"  {t['id']}  {t['status']:<12}{owner}  {t['subject']}{deps}")
    return 0


def _task_add(args) -> int:
    if not _require_team(args.team_id):
        return 2
    depends: Optional[List[str]] = None
    if args.depends.strip():
        depends = [d.strip() for d in args.depends.split(",") if d.strip()]
    tid = db.create_task(args.team_id, args.subject, description=args.desc, depends_on=depends, created_by="cli")
    print(f"task {tid}: added")
    return 0


def _task_claim(args) -> int:
    if not _require_team(args.team_id):
        return 2
    if db.claim_task(args.task_id, args.member):
        print(f"task {args.task_id}: claimed by {args.member}")
        return 0
    t = db.get_task(args.task_id)
    if t is None:
        print(f"team: no such task {args.task_id}", file=sys.stderr)
        return 2
    print(f"task {args.task_id}: NOT claimed (status={t['status']}, owner='{t['owner']}')")
    return 1


def _task_done(args) -> int:
    if not _require_team(args.team_id):
        return 2
    if db.complete_task(args.task_id):
        unblocked = [t["id"] for t in db.claimable_tasks(args.team_id)]
        print(f"task {args.task_id}: completed" + (f"   now claimable: {', '.join(unblocked)}" if unblocked else ""))
        return 0
    print(f"task {args.task_id}: could not complete", file=sys.stderr)
    return 1


def _send(args) -> int:
    if not _require_team(args.team_id):
        return 2
    mid = db.send_message(args.team_id, args.from_member, args.to_member, args.body)
    print(f"message {mid}: sent {args.from_member} → {args.to_member}")
    return 0


def _inbox(args) -> int:
    if not _require_team(args.team_id):
        return 2
    msgs = db.read_inbox(args.team_id, args.member, mark_read=not args.keep)
    if not msgs:
        print("(empty)")
        return 0
    for m in msgs:
        print(f"  [{m['from_member']} → {m['to_member']}] {m['body']}")
    return 0


def _shutdown(args) -> int:
    if not _require_team(args.team_id):
        return 2
    if coordinator.shutdown_teammate(args.team_id, args.member):
        print(f"teammate {args.member}: shut down")
        return 0
    print(f"team: no such teammate {args.member}", file=sys.stderr)
    return 1


def _cleanup(args) -> int:
    if not _require_team(args.team_id):
        return 2
    try:
        coordinator.cleanup_team(args.team_id, force=args.force)
    except RuntimeError as exc:
        print(f"team: {exc}", file=sys.stderr)
        return 1
    print(f"team {args.team_id}: cleaned up")
    return 0
