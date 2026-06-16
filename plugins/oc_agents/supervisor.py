"""Dispatch and control background agent sessions.

No long-lived daemon: ``dispatch`` spawns a detached worker process that runs
one session and self-reports to the shared DB; ``stop`` signals it; and the DB
read path (:func:`plugins.oc_agents.db.reconcile_liveness`) demotes any session
whose process died without finalizing. This mirrors how the repo's cron and
kanban dispatchers already work — detached subprocess + durable state.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import db

logger = logging.getLogger("hermes.plugins.oc_agents.supervisor")


def _hermes_entry() -> str:
    """Best-effort path to the hermes CLI entry for worker re-invocation."""
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).name in ("hermes", "oc") and Path(argv0).is_file():
        return str(Path(argv0).resolve())
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "hermes"
        if cand.is_file():
            return str(cand)
    return argv0 or "hermes"


def dispatch(
    prompt: str,
    *,
    name: str = "",
    cwd: str = "",
    model: str = "",
    provider: str = "",
    toolsets: Optional[List[str]] = None,
    parent_id: str = "",
    kind: str = "agent",
    extra_env: Optional[Dict[str, str]] = None,
) -> str:
    """Create a session row and spawn a detached worker. Returns the session id."""
    session_id = db.new_session_id()
    log_path = str(db.logs_dir() / f"{session_id}.log")
    resolved_cwd = cwd or os.getcwd()

    db.create_session(
        session_id=session_id, prompt=prompt, name=name, cwd=resolved_cwd,
        model=model, provider=provider, toolsets=toolsets, parent_id=parent_id,
        kind=kind, log_path=log_path,
    )

    cmd = [sys.executable, _hermes_entry(), "agents", "_worker", "--id", session_id]
    env = dict(os.environ)
    # Pin the worker to the same DB file we just wrote.
    env["HERMES_OC_AGENTS_DB"] = str(db.db_path())
    if extra_env:
        env.update(extra_env)

    creationflags = 0
    start_new_session = True
    try:
        from hermes_cli._subprocess_compat import windows_detach_flags

        cf = windows_detach_flags()
        if cf:
            creationflags = cf
            start_new_session = False
    except Exception:
        pass

    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            start_new_session=start_new_session, creationflags=creationflags,
            cwd=resolved_cwd,
        )
        db.set_pid(session_id, proc.pid)
    except Exception as exc:  # noqa: BLE001
        db.finish_session(session_id, db.STATE_FAILED, error=f"failed to spawn worker: {exc}")
        raise
    return session_id


def stop(session_id: str) -> bool:
    """Signal a running session's process and mark it stopped."""
    row = db.get_session(session_id)
    if row is None:
        return False
    if row["status"] not in db.LIVE_STATES:
        return False
    pid = row.get("pid")
    if pid:
        for sig in (signal.SIGTERM,):
            try:
                os.kill(int(pid), sig)
            except ProcessLookupError:
                break
            except Exception as exc:  # noqa: BLE001
                logger.debug("oc_agents: could not signal pid %s: %s", pid, exc)
    db.finish_session(session_id, db.STATE_STOPPED, error="stopped by user")
    return True


def reconcile() -> int:
    return db.reconcile_liveness()


def snapshot(include_done: bool = True, limit: int = 100) -> List[Dict[str, Any]]:
    """Reconcile liveness, then return the session list."""
    db.reconcile_liveness()
    return db.list_sessions(limit=limit, include_done=include_done)
