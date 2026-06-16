"""``hermes agents`` — dispatch and manage background agent sessions (Agent View).

    hermes agents dispatch "<prompt>" [--name N] [--cwd DIR] [--model M] [--toolsets a,b]
    hermes agents list [--all] [--json]
    hermes agents show <id>
    hermes agents logs <id> [--follow]
    hermes agents attach <id>        # follow logs live until the session ends
    hermes agents stop <id>
    hermes agents rm <id>
    hermes agents pin <id> [--off]

``dispatch`` returns immediately with a short id; the work runs in a detached
process. Liveness is reconciled on every ``list``/``show``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db, supervisor

_STATUS_GLYPH = {
    db.STATE_PENDING: "◦",
    db.STATE_WORKING: "✻",
    db.STATE_NEEDS_INPUT: "?",
    db.STATE_COMPLETED: "✓",
    db.STATE_FAILED: "✗",
    db.STATE_STOPPED: "■",
}


def setup(subparser) -> None:
    sub = subparser.add_subparsers(dest="agents_command", metavar="<command>")

    p = sub.add_parser("dispatch", help="Start a background agent session")
    p.add_argument("prompt", help="The task for the background agent")
    p.add_argument("--name", default="", help="Display name (default: derived from prompt)")
    p.add_argument("--cwd", default="", help="Working directory for the session")
    p.add_argument("--model", default="", help="Model override")
    p.add_argument("--provider", default="", help="Provider override")
    p.add_argument("--toolsets", default="", help="Comma-separated toolset override")
    p.add_argument("--attach", action="store_true", help="Follow the session live after dispatch")

    pl = sub.add_parser("list", help="List background sessions")
    pl.add_argument("--all", action="store_true", help="Include finished sessions")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="Show a session's details")
    ps.add_argument("id")
    ps.add_argument("--json", action="store_true")

    plog = sub.add_parser("logs", help="Show a session's output log")
    plog.add_argument("id")
    plog.add_argument("--follow", action="store_true", help="Stream until the session ends")

    pa = sub.add_parser("attach", help="Follow a session live until it ends")
    pa.add_argument("id")

    pstop = sub.add_parser("stop", help="Stop a running session")
    pstop.add_argument("id")

    prm = sub.add_parser("rm", help="Remove a session from the list")
    prm.add_argument("id")

    ppin = sub.add_parser("pin", help="Pin (or unpin) a session to the top")
    ppin.add_argument("id")
    ppin.add_argument("--off", action="store_true", help="Unpin instead")

    # Hidden worker entry used by the detached subprocess.
    pw = sub.add_parser("_worker", help=argparse.SUPPRESS)
    pw.add_argument("--id", required=True)


def handle(args) -> int:
    cmd = getattr(args, "agents_command", None)
    dispatch_map = {
        "dispatch": _cmd_dispatch,
        "list": _cmd_list,
        "show": _cmd_show,
        "logs": _cmd_logs,
        "attach": _cmd_attach,
        "stop": _cmd_stop,
        "rm": _cmd_rm,
        "pin": _cmd_pin,
        "_worker": _cmd_worker,
    }
    fn = dispatch_map.get(cmd or "")
    if fn is None:
        print("usage: hermes agents {dispatch|list|show|logs|attach|stop|rm|pin} ...", file=sys.stderr)
        return 2
    return fn(args)


def _cmd_dispatch(args) -> int:
    toolsets: Optional[List[str]] = None
    if args.toolsets.strip():
        toolsets = [t.strip() for t in args.toolsets.split(",") if t.strip()]
    try:
        sid = supervisor.dispatch(
            args.prompt, name=args.name, cwd=args.cwd, model=args.model,
            provider=args.provider, toolsets=toolsets,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"agents: dispatch failed: {exc}", file=sys.stderr)
        return 1
    print(f"agent {sid}: dispatched")
    print(f"  watch: hermes agents show {sid}   logs: hermes agents logs {sid}")
    if args.attach:
        return _follow(sid)
    return 0


def _cmd_list(args) -> int:
    sessions = supervisor.snapshot(include_done=bool(args.all))
    if getattr(args, "json", False):
        print(json.dumps(sessions, indent=2, default=str))
        return 0
    if not sessions:
        print("No background sessions. Start one with: hermes agents dispatch \"<task>\"")
        return 0
    c = db.counts()
    summary = "  ".join(f"{k}:{v}" for k, v in sorted(c.items()))
    print(f"Background sessions  ({summary})\n")
    print(f"{'':1} {'ID':<10} {'STATUS':<12} {'AGE':>6}  {'NAME'}")
    now = time.time()
    for s in sessions:
        glyph = _STATUS_GLYPH.get(s["status"], "·")
        age = _fmt_age(now - (s.get("created_at") or now))
        pin = "*" if s.get("pinned") else " "
        line = f"{glyph} {s['id']:<10} {s['status']:<12} {age:>6}  {pin}{s['name']}"
        print(line)
        if s.get("last_summary") and s["status"] in db.LIVE_STATES:
            print(f"   └ {s['last_summary'][:80]}")
    return 0


def _cmd_show(args) -> int:
    supervisor.reconcile()
    s = db.get_session(args.id)
    if not s:
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    if getattr(args, "json", False):
        print(json.dumps(s, indent=2, default=str))
        return 0
    print(f"Session {s['id']}  —  {s['name']}   [{s['status']}]")
    print(f"  prompt: {s['prompt'][:200]}")
    if s.get("cwd"):
        print(f"  cwd: {s['cwd']}")
    if s.get("model"):
        print(f"  model: {s['model']}")
    if s.get("pid"):
        print(f"  pid: {s['pid']}   api_calls: {s.get('api_calls', 0)}")
    if s.get("started_at"):
        dur = (s.get("ended_at") or time.time()) - s["started_at"]
        print(f"  duration: {dur:.1f}s")
    if s.get("last_summary"):
        print(f"  latest: {s['last_summary'][:200]}")
    if s.get("error"):
        print(f"  error: {s['error']}")
    if s.get("result"):
        print("\n  Result:")
        for line in str(s["result"]).splitlines()[:40]:
            print(f"    {line}")
    return 0


def _read_log(session_id: str) -> Optional[str]:
    s = db.get_session(session_id)
    if not s:
        return None
    lp = s.get("log_path")
    if lp and Path(lp).is_file():
        try:
            return Path(lp).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def _cmd_logs(args) -> int:
    if not db.get_session(args.id):
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    if args.follow:
        return _follow(args.id)
    text = _read_log(args.id)
    if text:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        print("(no log output yet)")
    return 0


def _cmd_attach(args) -> int:
    if not db.get_session(args.id):
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    return _follow(args.id)


def _follow(session_id: str, poll: float = 1.0, timeout: float = 1800.0) -> int:
    """Stream a session's log + status until it leaves a live state."""
    print(f"— attached to {session_id} (Ctrl-C to detach; the session keeps running) —")
    shown = 0
    start = time.time()
    last_status = ""
    try:
        while True:
            supervisor.reconcile()
            s = db.get_session(session_id)
            if not s:
                print("(session removed)")
                return 2
            text = _read_log(session_id) or ""
            if len(text) > shown:
                sys.stdout.write(text[shown:])
                sys.stdout.flush()
                shown = len(text)
            if s["status"] != last_status:
                last_status = s["status"]
            if s["status"] not in db.LIVE_STATES:
                print(f"\n— session {session_id}: {s['status']} —")
                if s.get("result") and not text:
                    print(s["result"][:2000])
                return 0 if s["status"] == db.STATE_COMPLETED else 1
            if time.time() - start > timeout:
                print("\n(attach timed out; session still running)")
                return 0
            time.sleep(poll)
    except KeyboardInterrupt:
        print(f"\n— detached from {session_id} (still running) —")
        return 0


def _cmd_stop(args) -> int:
    if supervisor.stop(args.id):
        print(f"agent {args.id}: stopped")
        return 0
    s = db.get_session(args.id)
    if not s:
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    print(f"agent {args.id}: already {s['status']}")
    return 0


def _cmd_rm(args) -> int:
    s = db.get_session(args.id)
    if not s:
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    if s["status"] in db.LIVE_STATES:
        print(f"agent {args.id} is {s['status']}; stop it first (hermes agents stop {args.id})", file=sys.stderr)
        return 1
    db.delete_session(args.id)
    print(f"agent {args.id}: removed")
    return 0


def _cmd_pin(args) -> int:
    if not db.get_session(args.id):
        print(f"agents: no such session {args.id}", file=sys.stderr)
        return 2
    db.set_pinned(args.id, not args.off)
    print(f"agent {args.id}: {'unpinned' if args.off else 'pinned'}")
    return 0


def _cmd_worker(args) -> int:
    from . import worker

    return worker.run_worker(args.id)


def _fmt_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


# Allow JSON snapshot for external consumers (e.g. a dashboard endpoint).
def json_snapshot(include_done: bool = True) -> List[Dict[str, Any]]:
    return supervisor.snapshot(include_done=include_done)
