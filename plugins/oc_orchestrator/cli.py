"""``hermes orchestrator`` — route a goal to specialized profiles and inspect state.

Subcommands:
    hermes orchestrator run "<goal>" [--profiles a,b,c] [--board NAME] [--model M]
        Route the goal (deterministic triage) and, for a parallelizable goal,
        decompose it into per-profile subtasks via the advisory brain (a real model
        if reachable, deterministic fallback otherwise), then assign each as a real
        Kanban card to its profile. Prints the plan.
    hermes orchestrator status [--tree RUN_TREE_ID]
        Show the cap-ledger reservation state and recent orchestrator decisions.

The brain is model-agnostic (gateway-backed) and always has a deterministic
fallback, so this command works even when no model is reachable.
"""

from __future__ import annotations

import argparse
from typing import List, Optional

DEFAULT_PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]


def setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="orchestrator_command", metavar="<command>")

    p_run = sub.add_parser("run", help="Route + decompose a goal to specialized profiles")
    p_run.add_argument("goal", help="The goal to orchestrate")
    p_run.add_argument("--profiles", default=None,
                       help="Comma-separated available profiles (default: the roster)")
    p_run.add_argument("--board", default="default", help="Kanban board to assign cards to")
    p_run.add_argument("--model", default="claude-sonnet-4-6", help="Brain model")
    p_run.add_argument("--no-assign", action="store_true",
                       help="Plan only; do not create Kanban cards")

    p_status = sub.add_parser("status", help="Show ledger reservations + recent decisions")
    p_status.add_argument("--tree", default=None, help="Filter to one run-tree id")


def _profiles(args) -> List[str]:
    if getattr(args, "profiles", None):
        return [p.strip() for p in args.profiles.split(",") if p.strip()]
    return list(DEFAULT_PROFILES)


def _cmd_run(args) -> int:
    from . import brain, kanban_bridge

    profiles = _profiles(args)
    # Brain with a deterministic fallback: try the live model, but route_decompose
    # falls back to deterministic slices if the model is unreachable.
    llm = None
    try:
        llm = brain.gateway_llm(model=args.model)
    except Exception:
        llm = None

    plan = brain.route_decompose(args.goal, profiles, llm=llm, max_fanout=5)
    print(f"goal: {args.goal}")
    print(f"shape: {plan['shape']}   lead: {plan['lead']}")
    print(f"rationale: {plan['rationale']}")
    if plan["shape"] == "single":
        subtasks = [{"profile": plan["lead"], "subtask": args.goal}]
    else:
        subtasks = plan["subtasks"]
    for st in subtasks:
        print(f"  - {st['profile']}: {st['subtask'][:90]}")

    if args.no_assign:
        print("(--no-assign: no Kanban cards created)")
        return 0

    try:
        from hermes_cli import kanban_db
        conn = kanban_db.connect()
        try:
            for st in subtasks:
                tid = kanban_bridge.assign_card(
                    conn, title=st["subtask"][:120], profile=st["profile"], board=args.board)
                print(f"  assigned card {tid} -> {st['profile']}")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"(could not assign Kanban cards: {exc})")
        return 1
    return 0


def _cmd_status(args) -> int:
    from . import db as odb

    tree = getattr(args, "tree", None)
    with odb.connect() as conn:
        q = "SELECT run_tree_id, status, COUNT(*) c FROM slot_reservations"
        params: list = []
        if tree:
            q += " WHERE run_tree_id=?"
            params.append(tree)
        q += " GROUP BY run_tree_id, status"
        rows = conn.execute(q, params).fetchall()
        print("=== slot reservations ===")
        if not rows:
            print("  (none)")
        for r in rows:
            print(f"  {r['run_tree_id']}: {r['status']} x{r['c']}")

        dq = "SELECT ts, run_tree_id, kind FROM orchestrator_decisions"
        if tree:
            dq += " WHERE run_tree_id=?"
        dq += " ORDER BY id DESC LIMIT 15"
        drows = conn.execute(dq, params if tree else []).fetchall()
        print("=== recent decisions ===")
        if not drows:
            print("  (none)")
        for d in drows:
            print(f"  {d['kind']}  tree={d['run_tree_id']}")
    return 0


def handle(args) -> int:
    cmd = getattr(args, "orchestrator_command", None)
    if cmd == "run":
        return _cmd_run(args)
    if cmd == "status":
        return _cmd_status(args)
    print("usage: hermes orchestrator <run|status> ...")
    return 2
