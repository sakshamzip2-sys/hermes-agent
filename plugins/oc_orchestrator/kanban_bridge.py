"""Coordinate specialized profiles through Kanban, projected into the run spine.

The Hermes-native coordination substrate is Kanban (hermes_cli.kanban_db), not
naive delegate_task: the orchestrator assigns a card to a profile (the card's
assignee), and the worker for that card runs under that profile's identity. This
bridge does two things:

  1. assign_card: create a Kanban card assigned to a profile and announce it on the
     run-event spine, so the parallel view shows it.
  2. sync_board_to_spine: project every card onto the spine as a run, namespaced
     ``kanban:<board>:<task_id>`` and parented to ``profiles:<assignee>``, with the
     Kanban status mapped into the one normalized state vocabulary.

The existing projection fold then renders cards under their profiles in the
cockpit with no extra UI code. We do not reimplement Kanban; we read it and emit.
"""

from __future__ import annotations

from typing import Optional

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events as ev

# Kanban status -> the spine's normalized state vocabulary.
KANBAN_TO_NORMAL = {
    "triage": "pending",
    "todo": "pending",
    "ready": "pending",
    "running": "running",
    "reclaimed": "running",
    "blocked": "stalled",
    "done": "completed",
}


def card_run_id(board: str, task_id: str) -> str:
    return f"kanban:{board}:{task_id}"


def profile_run_id(profile: str) -> str:
    return f"profiles:{profile}"


def _emit_card(task, board: str) -> None:
    assignee = getattr(task, "assignee", None)
    status = (getattr(task, "status", "") or "").lower()
    norm = KANBAN_TO_NORMAL.get(status, "unknown")
    rid = card_run_id(board, task.id)
    parent = profile_run_id(assignee) if assignee else None
    title = getattr(task, "title", "")

    if norm == "completed":
        spine_db.append_event(ev.build_event(
            rid, ev.RUN_COMPLETED, source="kanban", parent_run_id=parent,
            agent_id=assignee, payload={"title": title, "status": status, "assignee": assignee},
            dedupe_key="kanban:terminal"))
    elif norm == "stalled":
        spine_db.append_event(ev.build_event(
            rid, ev.RUN_STALLED, source="kanban", parent_run_id=parent,
            agent_id=assignee, payload={"title": title, "status": status, "reason": "blocked",
                                        "assignee": assignee},
            dedupe_key="kanban:stalled"))
    else:
        # Non-terminal: emit a status reflecting the normalized state. No dedupe so
        # the latest sync wins by seq in the fold (correct over flip-flops).
        spine_db.append_event(ev.build_event(
            rid, ev.RUN_STATUS, source="kanban", parent_run_id=parent,
            agent_id=assignee, payload={"title": title, "status": norm, "assignee": assignee}))


def assign_card(
    conn,
    *,
    title: str,
    profile: str,
    body: Optional[str] = None,
    board: str = "default",
    priority: int = 0,
) -> str:
    """Create a Kanban card assigned to a profile and announce it on the spine.
    Returns the new task id."""
    from hermes_cli import kanban_db

    task_id = kanban_db.create_task(
        conn, title=title, body=body, assignee=profile,
        created_by="orchestrator", priority=priority,
    )
    rid = card_run_id(board, task_id)
    spine_db.append_event(ev.build_event(
        rid, ev.RUN_CREATED, source="kanban", parent_run_id=profile_run_id(profile),
        agent_id=profile, payload={"title": title, "assignee": profile}, dedupe_key="created"))
    task = kanban_db.get_task(conn, task_id)
    if task is not None:
        _emit_card(task, board)
    return task_id


def sync_board_to_spine(conn, board: str = "default") -> int:
    """Project every card on the board onto the spine as a run under its assigned
    profile. Returns the number of cards projected."""
    from hermes_cli import kanban_db

    tasks = kanban_db.list_tasks(conn)
    for t in tasks:
        _emit_card(t, board)
    return len(tasks)
