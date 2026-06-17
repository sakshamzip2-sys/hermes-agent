"""``hermes dream-all`` terminal subcommand for unified dreaming.

Subcommands:
  status   combined health of local + honcho + gbrain + last orchestrated run
  run      run every enabled dreamer now (``--force`` bypasses local debounce)
  plan     dry-run — probe + report what would happen, write nothing
"""

from __future__ import annotations

import argparse


def setup(subparser: argparse.ArgumentParser) -> None:
    sub = subparser.add_subparsers(dest="dream_all_cmd")

    p_status = sub.add_parser("status", help="combined status of all dreamers")
    p_status.set_defaults(func=_cmd_status)

    p_run = sub.add_parser("run", help="run every enabled dreamer now")
    p_run.add_argument("--force", action="store_true",
                       help="bypass the local dreamer's debounce interval")
    p_run.set_defaults(func=_cmd_run)

    p_plan = sub.add_parser("plan", help="dry-run: show what would happen")
    p_plan.set_defaults(func=_cmd_plan)

    # Default when bare `hermes dream-all` is invoked.
    subparser.set_defaults(func=_cmd_status)


def handle(args: argparse.Namespace) -> int:
    func = getattr(args, "func", None)
    if func is None:
        return _cmd_status(args)
    return func(args)


def _cmd_status(args: argparse.Namespace) -> int:
    from . import render_status, status

    print(render_status(status()))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from . import render_run, run_all

    force = bool(getattr(args, "force", False))
    print(render_run(run_all(force=force)))
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    from . import render_run, run_all

    print(render_run(run_all(plan=True)))
    return 0
