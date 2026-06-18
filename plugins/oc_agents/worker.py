"""Detached worker that runs one background agent session.

Spawned by :mod:`plugins.oc_agents.supervisor` as a separate, detached process
(``hermes agents _worker --id <id>``). It reads the session row, builds a
headless ``AIAgent`` exactly like ``hermes -z`` (oneshot) does, runs one
conversation, streams a cheap "latest activity" summary into the DB so the row
stays informative, and records the final result/status.

Output (the agent's own stdout/stderr) is redirected to the session's log file
so ``hermes agents logs <id>`` can show it.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from typing import Any, Dict, List, Optional

from . import db


def _bg_clarify_callback(question: str, choices=None) -> str:
    """Clarify is disabled for unattended background sessions — instruct the
    agent to pick a sensible default and continue rather than stall."""
    if choices:
        return (
            f"[background session: no user available. Pick the best option from "
            f"{choices} using your own judgment and continue.]"
        )
    return (
        "[background session: no user available. Make the most reasonable "
        "assumption you can and continue.]"
    )


class _FakeAgent:
    """Deterministic stand-in used when OC_AGENTS_FAKE_AGENT is set.

    Lets the entire detached-worker path (spawn → run → DB finalize) be verified
    offline with no credentials or token spend.
    """

    session_id = ""

    def __init__(self, prompt_echo: str = "") -> None:
        self._echo = prompt_echo

    def run_conversation(
        self, prompt: str, task_id: str = "", **_kwargs: Any
    ) -> Dict[str, Any]:
        # Accept (and ignore) extra kwargs like conversation_history so the
        # follow-up/continuation path works under the fake agent too.
        return {
            "messages": [{"role": "assistant", "content": f"[fake] handled: {prompt[:80]}"}],
            "completed": True,
            "api_calls": 1,
        }


def _build_headless_agent(row: Dict[str, Any]):
    """Construct an AIAgent with full config/credential resolution (oneshot-style)."""
    if os.getenv("OC_AGENTS_FAKE_AGENT", "").strip().lower() in ("1", "true", "yes", "on"):
        return _FakeAgent()

    import json

    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from hermes_cli.tools_config import _get_platform_tools
    from run_agent import AIAgent

    cfg = load_config()
    model_cfg = cfg.get("model") or {}
    cfg_model = model_cfg if isinstance(model_cfg, str) else (model_cfg.get("default") or model_cfg.get("model") or "")
    effective_model = (row.get("model") or "").strip() or os.getenv("HERMES_INFERENCE_MODEL", "").strip() or cfg_model
    effective_provider = (row.get("provider") or "").strip() or None

    if effective_provider is None and row.get("model"):
        try:
            from hermes_cli.models import detect_provider_for_model

            cfg_provider = model_cfg.get("provider") if isinstance(model_cfg, dict) else ""
            current = (cfg_provider or os.getenv("HERMES_INFERENCE_PROVIDER", "") or "auto").strip().lower()
            detected = detect_provider_for_model(effective_model, current)
            if detected:
                effective_provider, effective_model = detected
        except Exception:
            pass

    runtime = resolve_runtime_provider(requested=effective_provider, target_model=effective_model or None)

    toolsets: Optional[List[str]] = None
    raw_ts = row.get("toolsets")
    if raw_ts:
        try:
            toolsets = json.loads(raw_ts)
        except Exception:
            toolsets = None
    if toolsets is None:
        try:
            toolsets = sorted(_get_platform_tools(cfg, "cli"))
        except Exception:
            toolsets = None

    session_db = None
    try:
        from hermes_state import SessionDB

        session_db = SessionDB()
    except Exception:
        session_db = None

    # Honour the same fallback chain as a normal CLI/oneshot turn so a rate-
    # limited or unavailable model degrades gracefully instead of failing.
    fallback_model = None
    try:
        from hermes_cli.fallback_config import get_fallback_chain

        fallback_model = get_fallback_chain(cfg) or None
    except Exception:
        fallback_model = None

    kwargs: Dict[str, Any] = {
        "api_key": runtime.get("api_key"),
        "base_url": runtime.get("base_url"),
        "provider": runtime.get("provider"),
        "api_mode": runtime.get("api_mode"),
        "model": effective_model,
        "enabled_toolsets": toolsets,
        "quiet_mode": True,
        "platform": "cli",
        "session_db": session_db,
        "credential_pool": runtime.get("credential_pool"),
        "fallback_model": fallback_model,
        # No user is attached to a background session, so clarify must resolve
        # itself rather than stall (mirrors oneshot's synthetic responder).
        "clarify_callback": _bg_clarify_callback,
        "skip_context_files": True,
    }

    sid = row["id"]

    def _clarify(question: str, choices=None) -> str:
        # If the user has queued a message for this running agent, deliver it as
        # the answer (live steering); otherwise fall back to the autonomous
        # default so the session never stalls.
        try:
            pending = db.pop_inbox_message(sid)
        except Exception:
            pending = None
        if pending:
            try:
                db.add_event(sid, f"user message: {pending}", kind="user")
            except Exception:
                pass
            return pending
        return _bg_clarify_callback(question, choices)

    kwargs["clarify_callback"] = _clarify

    def _progress(*args, **_kwargs) -> None:
        # AIAgent's tool_progress_callback is called on tool start/complete/
        # thinking. We don't depend on its exact shape — extract any string-ish
        # activity and stash it as the row summary. Must never raise.
        try:
            parts = [str(a) for a in args if a is not None and not isinstance(a, (dict, list))]
            summary = " ".join(p for p in parts if p).strip()
            if not summary and args:
                summary = str(args[0])[:200]
            if summary:
                db.update_summary(sid, summary[:200])
                # Also persist a granular event so the cockpit can show a live
                # play-by-play, not just the latest one-line summary. Derive a
                # coarse kind from the activity prefix so the UI can colour-code
                # tool calls vs reasoning vs other steps.
                low = summary.lower()
                if low.startswith("tool."):
                    kind = "tool"
                elif low.startswith("reasoning") or "_thinking" in low:
                    kind = "thinking"
                else:
                    kind = "activity"
                db.add_event(sid, summary[:500], kind=kind)
        except Exception:
            pass

    # Best-effort: attach the progress callback; retry without it if the
    # constructor doesn't accept the kwarg on this build.
    try:
        agent = AIAgent(tool_progress_callback=_progress, **kwargs)
    except TypeError:
        agent = AIAgent(**kwargs)

    for attr, val in (("suppress_status_output", True), ("stream_delta_callback", None), ("tool_gen_callback", None)):
        try:
            setattr(agent, attr, val)
        except Exception:
            pass
    return agent


def _final_text(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict):
        return str(result or "")
    for key in ("final_response", "final", "response", "content"):
        v = result.get(key)
        if isinstance(v, str) and v.strip():
            return v
    for msg in reversed(result.get("messages") or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                joined = "".join(c.get("text", "") for c in content if isinstance(c, dict)).strip()
                if joined:
                    return joined
    return ""


def _apply_startup_permission_mode():
    """Honor a permission mode forwarded by the spawner via HERMES_PERMISSION_MODE.

    Lets a teammate (or any background session) start in e.g. ``plan`` mode —
    read-only until the lead approves — by activating the permission_rules engine
    process-wide. Returns the applied mode, or None when unset/unavailable.
    """
    raw = os.environ.get("HERMES_PERMISSION_MODE", "").strip()
    if not raw:
        return None
    try:
        from tools.permission_rules import normalize_mode, set_global_mode

        mode = normalize_mode(raw)
        set_global_mode(mode)
        return mode
    except Exception:  # noqa: BLE001 — never let a bad mode wedge the worker
        return None


def run_worker(session_id: str) -> int:
    """Run one background session to completion. Returns a process exit code."""
    os.environ.setdefault("HERMES_YOLO_MODE", "1")
    os.environ.setdefault("HERMES_ACCEPT_HOOKS", "1")
    # Sandbox-by-default: this background runner auto-approves, so it must NOT run
    # model-authored code on the host. 'auto' resolves to docker/modal when present
    # and only downgrades to local with a logged warning. An inherited TERMINAL_ENV
    # (e.g. gateway-resolved docker) wins via setdefault.
    os.environ.setdefault("TERMINAL_ENV", "auto")
    _apply_startup_permission_mode()
    logging.disable(logging.CRITICAL)

    row = db.get_session(session_id)
    if row is None:
        return 2

    db.set_pid(session_id, os.getpid())
    log_path = row.get("log_path") or str(db.logs_dir() / f"{session_id}.log")

    try:
        logf = open(log_path, "w", encoding="utf-8")
    except Exception:
        logf = open(os.devnull, "w", encoding="utf-8")

    task_id = f"bgsession-{session_id}"
    cwd = row.get("cwd")
    if cwd:
        try:
            from tools.terminal_tool import register_task_env_overrides

            register_task_env_overrides(task_id, {"cwd": cwd})
        except Exception:
            pass

    try:
        with redirect_stdout(logf), redirect_stderr(logf):
            try:
                agent = _build_headless_agent(row)
            except Exception as exc:  # noqa: BLE001
                db.finish_session(session_id, db.STATE_FAILED, error=f"agent build failed: {exc}")
                return 1

            db.mark_working(session_id, agent_session_id=getattr(agent, "session_id", "") or "")
            try:
                result = agent.run_conversation(row["prompt"], task_id=task_id)
            except Exception as exc:  # noqa: BLE001
                db.finish_session(session_id, db.STATE_FAILED, error=f"{type(exc).__name__}: {exc}")
                return 1

            # Drain any messages the user queued while/after the run as follow-up
            # turns (preserving conversation history) so a background agent can be
            # steered or given more work. Bounded to avoid a runaway loop.
            for _ in range(20):
                try:
                    pending = db.pop_inbox_message(session_id)
                except Exception:  # noqa: BLE001
                    pending = None
                if not pending:
                    break
                try:
                    db.add_event(session_id, f"user message: {pending}", kind="user")
                except Exception:  # noqa: BLE001
                    pass
                db.mark_working(session_id, agent_session_id=getattr(agent, "session_id", "") or "")
                history = result.get("messages") if isinstance(result, dict) else None
                try:
                    if history:
                        result = agent.run_conversation(
                            pending, task_id=task_id, conversation_history=history
                        )
                    else:
                        result = agent.run_conversation(pending, task_id=task_id)
                except Exception as exc:  # noqa: BLE001
                    db.finish_session(
                        session_id, db.STATE_FAILED, error=f"follow-up failed: {exc}"
                    )
                    return 1

        text = _final_text(result if isinstance(result, dict) else {})
        api_calls = int(result.get("api_calls") or 0) if isinstance(result, dict) else 0
        completed = result.get("completed", True) if isinstance(result, dict) else True
        status = db.STATE_COMPLETED if completed else db.STATE_FAILED
        db.update_summary(session_id, (text[:200] or "done"), api_calls=api_calls)
        db.finish_session(
            session_id, status,
            result=text[:8000], api_calls=api_calls,
            error="" if completed else "run did not complete",
        )
        return 0
    finally:
        try:
            logf.close()
        except Exception:
            pass


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="oc_agents-worker")
    parser.add_argument("--id", required=True)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return run_worker(args.id)


if __name__ == "__main__":
    raise SystemExit(main())
