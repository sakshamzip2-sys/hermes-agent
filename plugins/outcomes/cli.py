"""``hermes outcomes`` terminal subcommand.

Subcommands:
  status   show config + recent mean turn_score + recorded count
  run      emit a cycle summary now
"""

from __future__ import annotations

import argparse


def setup(subparser: argparse.ArgumentParser) -> None:
    sub = subparser.add_subparsers(dest="outcomes_cmd")

    p_status = sub.add_parser("status", help="show outcomes config and recent scores")
    p_status.set_defaults(func=_cmd_status)

    p_run = sub.add_parser("run", help="emit a cycle summary now")
    p_run.set_defaults(func=_cmd_run)

    subparser.set_defaults(func=_cmd_status)


def handle(args: argparse.Namespace) -> int:
    func = getattr(args, "func", None)
    if func is None:
        return _cmd_status(args)
    return func(args)


def _cmd_status(args: argparse.Namespace) -> int:
    from . import _render_status

    print(_render_status())
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from . import run_cycle

    summary = run_cycle()
    mean = summary.get("mean_recent")
    mean_s = f"{mean:.3f}" if isinstance(mean, (int, float)) else "n/a"
    print(
        f"Outcomes cycle: recorded={summary.get('recorded', 0)} "
        f"recent_n={summary.get('recent_n', 0)} mean_recent={mean_s}"
    )
    return 0
