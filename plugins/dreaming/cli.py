"""``oc dream`` terminal subcommand for the dreaming plugin.

Subcommands:
  status   show config, last run, and recent pass counts
  run      run a consolidation pass now (``--force`` to bypass debounce)
  dreams   list the DREAMS.md holding pen
"""

from __future__ import annotations

import argparse
import asyncio


def setup(subparser: argparse.ArgumentParser) -> None:
    sub = subparser.add_subparsers(dest="dream_cmd")

    p_status = sub.add_parser("status", help="show dreaming config and recent runs")
    p_status.set_defaults(func=_cmd_status)

    p_run = sub.add_parser("run", help="run a consolidation pass now")
    p_run.add_argument("--force", action="store_true", help="bypass enabled flag and debounce")
    p_run.set_defaults(func=_cmd_run)

    p_dreams = sub.add_parser("dreams", help="list the DREAMS.md holding pen")
    p_dreams.set_defaults(func=_cmd_dreams)

    # Default when bare `oc dream` is invoked.
    subparser.set_defaults(func=_cmd_status)


def handle(args: argparse.Namespace) -> int:
    func = getattr(args, "func", None)
    if func is None:
        return _cmd_status(args)
    return func(args)


def _cmd_status(args: argparse.Namespace) -> int:
    from .config import load_dreaming_config
    from .llm import aux_client_available
    from .runner import _store_path
    from .store import DreamStore

    cfg = load_dreaming_config()
    store = DreamStore(_store_path())
    last = store.last_run_ts()
    print("Dreaming (memory consolidation)")
    print(f"  enabled:            {cfg.enabled}")
    print(f"  aux provider ready: {aux_client_available()}")
    print(f"  min interval:       {cfg.min_interval_hours}h")
    print(f"  score threshold:    {cfg.engine.score_threshold}")
    print(f"  min recall:         {cfg.engine.min_recall_count} "
          f"(gate {'on' if cfg.engine.recall_gate_enabled else 'off'})")
    print(f"  diversity threshold:{cfg.engine.diversity_threshold}")
    print(f"  supersede:          {cfg.engine.supersede_enabled}")
    if last:
        import datetime as _dt
        when = _dt.datetime.fromtimestamp(last).isoformat(timespec="seconds")
        print(f"  last run:           {when}")
    else:
        print("  last run:           never")
    runs = store.recent_runs(limit=5)
    if runs:
        print("  recent passes:")
        for r in runs:
            print(f"    promoted={r.get('promoted',0)} updated={r.get('updated',0)} "
                  f"held={r.get('held',0)} dropped={r.get('dropped',0)} "
                  f"evaluated={r.get('evaluated',0)}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_dream_cycle

    force = bool(getattr(args, "force", False))
    summary = asyncio.run(run_dream_cycle(force=force))
    c = summary.counts()
    print(f"Dream cycle complete: promoted={c['promoted']} updated={c['updated']} "
          f"held={c['held']} dropped={c['dropped']} evaluated={c['evaluated']}")
    for r in summary.promoted:
        print(f"  + {r.candidate.raw_text}")
    for r in summary.updated:
        print(f"  ~ {r.candidate.raw_text}")
    return 0


def _cmd_dreams(args: argparse.Namespace) -> int:
    from . import memory_io

    entries = memory_io.read_dreams_entries()
    if not entries:
        print("DREAMS.md is empty.")
        return 0
    print(f"DREAMS.md holding pen ({len(entries)} entr{'y' if len(entries)==1 else 'ies'}):")
    for e in entries:
        print(f"  {e}")
    return 0
