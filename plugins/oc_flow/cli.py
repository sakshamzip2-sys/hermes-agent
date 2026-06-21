"""``oc flow`` — run and inspect dynamic workflows.

Subcommands:
    oc flow run <script.py> [--args JSON] [--background] [--resume RUN_ID]
                                [--concurrency N]
    oc flow list
    oc flow show <run_id>
    oc flow logs <run_id>
    oc flow stop <run_id>
    oc flow examples            # list bundled example flows

Set ``OC_FLOW_FAKE_AGENT=1`` to run with a deterministic no-LLM runner — useful
for smoke-testing the machinery without spending tokens.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import db


def setup(subparser) -> None:
    """Populate the ``oc flow`` argparse subparser."""
    sub = subparser.add_subparsers(dest="flow_command", metavar="<command>")

    p_run = sub.add_parser("run", help="Run a flow script")
    p_run.add_argument("script", help="Path to the flow .py script")
    p_run.add_argument("--args", default=None, help="JSON value passed to the flow as `args`")
    p_run.add_argument("--background", action="store_true", help="Run detached; returns a run id")
    p_run.add_argument("--resume", default=None, metavar="RUN_ID", help="Resume a prior run (cached agents skipped)")
    p_run.add_argument("--concurrency", type=int, default=None, help="Max concurrent subagents")
    p_run.add_argument("--quiet", action="store_true", help="Suppress live progress lines")
    # Hidden flag used by the detached worker re-invocation.
    p_run.add_argument("--_worker-run-id", default=None, help=argparse_suppress())

    p_list = sub.add_parser("list", help="List recent flow runs")
    p_list.add_argument("--limit", type=int, default=30)
    p_list.add_argument("--json", action="store_true")

    p_show = sub.add_parser("show", help="Show a run's phases, agents, and result")
    p_show.add_argument("run_id")
    p_show.add_argument("--json", action="store_true")

    p_logs = sub.add_parser("logs", help="Show a run's log lines")
    p_logs.add_argument("run_id")

    p_stop = sub.add_parser("stop", help="Stop a running flow")
    p_stop.add_argument("run_id")

    sub.add_parser("examples", help="List bundled example flows")


def argparse_suppress():
    import argparse

    return argparse.SUPPRESS


def handle(args) -> int:
    cmd = getattr(args, "flow_command", None)
    if cmd == "run":
        return _cmd_run(args)
    if cmd == "list":
        return _cmd_list(args)
    if cmd == "show":
        return _cmd_show(args)
    if cmd == "logs":
        return _cmd_logs(args)
    if cmd == "stop":
        return _cmd_stop(args)
    if cmd == "examples":
        return _cmd_examples(args)
    print("usage: oc flow {run|list|show|logs|stop|examples} ...", file=sys.stderr)
    return 2


def _parse_args_value(raw: Optional[str]) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw  # treat as a plain string


def _cmd_run(args) -> int:
    from .runtime import run_flow

    script = args.script
    if not args.resume and not Path(script).is_file():
        print(f"flow: script not found: {script}", file=sys.stderr)
        return 2

    flow_args = _parse_args_value(args.args)

    if args.background and not getattr(args, "_worker_run_id", None):
        return _spawn_background(args, flow_args)

    run_id = getattr(args, "_worker_run_id", None) or args.resume

    quiet = bool(getattr(args, "quiet", False))

    def progress(msg: str) -> None:
        if not quiet:
            print(f"  · {msg}", flush=True)

    if not quiet:
        print(f"flow: running {Path(script).name} ...", flush=True)

    outcome = run_flow(
        script_path=script if not args.resume else (db.get_run(args.resume) or {}).get("script_path") or script,
        args=flow_args,
        run_id=run_id,
        background=bool(args.background),
        resume=bool(args.resume),
        progress=progress,
        max_concurrency=args.concurrency or 8,
    )

    if outcome.status == "completed":
        print(f"\nflow {outcome.run_id}: completed ({outcome.agent_count} agents)")
        _print_result(outcome.result)
        return 0
    print(f"\nflow {outcome.run_id}: {outcome.status}", file=sys.stderr)
    if outcome.error:
        print(f"  error: {outcome.error}", file=sys.stderr)
    return 1


def _spawn_background(args, flow_args: Any) -> int:
    """Detach a worker that runs the flow and writes to the shared DB."""
    import subprocess

    run_id = db.new_run_id()
    # Pre-create the run row so `flow list` shows it immediately.
    source = Path(args.script).read_text(encoding="utf-8")
    from .runtime import extract_meta
    import hashlib

    meta = extract_meta(source)
    db.create_run(
        run_id=run_id,
        name=str(meta.get("name") or Path(args.script).stem),
        description=str(meta.get("description") or ""),
        script_path=str(Path(args.script).resolve()),
        script_sha=hashlib.sha256(source.encode()).hexdigest()[:16],
        args=flow_args, background=True, meta=meta,
    )

    cmd = [
        sys.executable, _hermes_entry(), "flow", "run",
        str(Path(args.script).resolve()),
        "--_worker-run-id", run_id, "--quiet",
    ]
    if args.args is not None:
        cmd += ["--args", args.args]
    if args.concurrency:
        cmd += ["--concurrency", str(args.concurrency)]

    env = dict(os.environ)
    # Make sure the worker resolves the same DB file we just wrote.
    env["HERMES_OC_FLOW_DB"] = str(db.db_path())

    creationflags = 0
    start_new_session = True
    try:
        from hermes_cli._subprocess_compat import windows_detach_flags

        creationflags = windows_detach_flags()
        start_new_session = False  # Windows uses creationflags instead
    except Exception:
        pass

    try:
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=start_new_session, creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        db.finish_run(run_id, "failed", error=f"failed to spawn worker: {exc}")
        print(f"flow: could not start background worker: {exc}", file=sys.stderr)
        return 1

    print(f"flow {run_id}: started in background")
    print(f"  watch:  oc flow show {run_id}")
    print(f"  logs:   oc flow logs {run_id}")
    return 0


def _hermes_entry() -> str:
    """Best-effort path to the hermes CLI entry script for re-invocation."""
    # Prefer the script that launched us (the `hermes` shim) when sensible.
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).name in ("hermes", "oc") and Path(argv0).is_file():
        return str(Path(argv0).resolve())
    # Fall back to the repo shim next to this package.
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "hermes"
        if cand.is_file():
            return str(cand)
    return argv0 or "hermes"


def _cmd_list(args) -> int:
    runs = db.list_runs(limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(runs, indent=2, default=str))
        return 0
    if not runs:
        print("No flow runs yet. Try: oc flow run <script.py>")
        return 0
    print(f"{'RUN ID':<18} {'STATUS':<11} {'AGENTS':>6}  {'NAME'}")
    for r in runs:
        print(f"{r['id']:<18} {r['status']:<11} {r['agent_count']:>6}  {r['name']}")
    return 0


def _cmd_show(args) -> int:
    run = db.get_run(args.run_id)
    if not run:
        print(f"flow: no such run {args.run_id}", file=sys.stderr)
        return 2
    phases = db.list_phases(args.run_id)
    agents = db.list_agents(args.run_id)
    if getattr(args, "json", False):
        print(json.dumps({"run": run, "phases": phases, "agents": agents}, indent=2, default=str))
        return 0

    print(f"Flow {run['id']}  —  {run['name']}")
    if run.get("description"):
        print(f"  {run['description']}")
    print(f"  status: {run['status']}   agents: {run['agent_count']}   phases: {run['phase_count']}")
    if run.get("started_at"):
        dur = (run.get("ended_at") or time.time()) - run["started_at"]
        print(f"  duration: {dur:.1f}s")
    if run.get("error"):
        print(f"  error: {run['error']}")
    if phases:
        print("\n  Phases:")
        for ph in phases:
            n = sum(1 for a in agents if (a.get("phase") or "") == ph["title"])
            print(f"    {ph['seq']}. {ph['title']}  ({n} agents)")
    if agents:
        print("\n  Agents:")
        for a in agents:
            label = a.get("label") or a.get("phase") or ""
            print(f"    #{a['call_index']:<3} {a['status']:<10} {label}")
    print()
    _print_result(db.decode_result(run))
    return 0


def _cmd_logs(args) -> int:
    if not db.get_run(args.run_id):
        print(f"flow: no such run {args.run_id}", file=sys.stderr)
        return 2
    for entry in db.list_logs(args.run_id):
        ts = time.strftime("%H:%M:%S", time.localtime(entry["ts"]))
        print(f"  {ts}  {entry['message']}")
    return 0


def _cmd_stop(args) -> int:
    run = db.get_run(args.run_id)
    if not run:
        print(f"flow: no such run {args.run_id}", file=sys.stderr)
        return 2
    if run["status"] not in ("running", "pending"):
        print(f"flow {args.run_id}: already {run['status']}")
        return 0
    pid = run.get("pid")
    if pid and run.get("background"):
        try:
            os.kill(int(pid), 15)  # SIGTERM
        except Exception as exc:  # noqa: BLE001
            print(f"flow: could not signal pid {pid}: {exc}", file=sys.stderr)
    db.finish_run(args.run_id, "stopped", error="stopped by user")
    print(f"flow {args.run_id}: stopped")
    return 0


def _cmd_examples(args) -> int:
    examples_dir = Path(__file__).resolve().parent / "examples"
    files = sorted(examples_dir.glob("*.py")) if examples_dir.is_dir() else []
    if not files:
        print("No bundled examples found.")
        return 0
    print("Bundled example flows (run with: oc flow run <path>):")
    for f in files:
        print(f"  {f}")
    return 0


def _print_result(result: Any) -> None:
    if result is None:
        return
    print("Result:")
    try:
        text = json.dumps(result, indent=2, default=str)
    except Exception:
        text = str(result)
    for line in text.splitlines()[:60]:
        print(f"  {line}")
