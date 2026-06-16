"""Plugin-declared background monitors (model-agnostic).

Ports the Claude Code "monitors" concept into OpenComputer v2: a plugin can
declare background commands whose matching output lines are streamed back to the
agent so it can react (tail a log, poll CI/a PR, watch a directory).

The streaming RUNTIME already exists — ``tools/process_registry`` runs a process
in the background, scans each output chunk against ``watch_patterns`` (rate
limited + circuit-broken), and pushes ``watch_match`` events onto a shared
``completion_queue`` that the gateway drains and injects into the conversation as
``[IMPORTANT: ...]`` messages.  This module adds only the *declarative surface*
and *lifecycle* the Claude Code concept layers on top: discover monitors from
enabled plugins' manifests and start/stop them.

Declared in ``plugin.yaml``::

    monitors:
      - name: ci-poller
        command: "while true; do curl -s $CI/status; sleep 30; done"
        watch_patterns: ["FAILED", "PASSED"]
        when: always                  # or "on-skill-invoke:<skill>"
        notify_on_complete: false

Model-agnostic: pure subprocess + string pattern matching; no provider/model
coupling anywhere.  Config-gated by enabling the owning plugin (no new env var).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Monitor key ("<plugin-key>:<monitor-name>") -> background process session id.
_started: Dict[str, str] = {}
_lock = threading.RLock()

_MONITOR_SESSION_KEY = "__monitors__"


def _normalize_when(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return "always"
    return value.strip().lower()


def _coerce_patterns(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(p) for p in value if str(p).strip()]
    return []


def start_plugin_monitors(
    when_values: Sequence[str] = ("always",),
    plugin_manager: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Start background monitors declared by enabled plugins.

    ``when_values`` filters which monitors to start (``("always",)`` at
    startup; ``("on-skill-invoke:<skill>",)`` when that skill first runs).
    Idempotent: a monitor already running (tracked by key) is not restarted.
    Returns a list of descriptors for the monitors started by this call.
    """
    when_set = {str(w).strip().lower() for w in when_values}
    try:
        from hermes_cli.plugins import get_plugin_manager
        from tools.process_registry import process_registry
    except Exception as exc:  # noqa: BLE001
        logger.debug("monitor_manager: dependencies unavailable: %s", exc)
        return []

    mgr = plugin_manager or get_plugin_manager()
    started: List[Dict[str, Any]] = []

    with _lock:
        for loaded in list(getattr(mgr, "_plugins", {}).values()):
            if not getattr(loaded, "enabled", False):
                continue
            manifest = getattr(loaded, "manifest", None)
            monitors = list(getattr(manifest, "monitors", None) or [])
            if not monitors:
                continue
            plugin_key = getattr(manifest, "key", "") or getattr(manifest, "name", "?")
            for mon in monitors:
                if not isinstance(mon, dict):
                    continue
                command = str(mon.get("command") or "").strip()
                if not command:
                    continue
                if _normalize_when(mon.get("when")) not in when_set:
                    continue
                name = str(mon.get("name") or "monitor").strip() or "monitor"
                key = f"{plugin_key}:{name}"
                if key in _started:
                    continue  # already running
                try:
                    session = process_registry.spawn_local(
                        command,
                        task_id=f"monitor:{key}",
                        session_key=_MONITOR_SESSION_KEY,
                    )
                    # Set watch patterns on the live session so matching output
                    # lines stream back to the agent via the existing runtime.
                    session.watch_patterns = _coerce_patterns(mon.get("watch_patterns"))
                    if mon.get("notify_on_complete"):
                        try:
                            session.notify_on_complete = True
                        except Exception:  # noqa: BLE001
                            pass
                    _started[key] = session.id
                    started.append(
                        {
                            "key": key,
                            "session_id": session.id,
                            "command": command,
                            "watch_patterns": list(session.watch_patterns),
                            "when": _normalize_when(mon.get("when")),
                        }
                    )
                    logger.info("Started plugin monitor %s: %s", key, command[:80])
                except Exception as exc:  # noqa: BLE001 — one bad monitor must not abort the rest
                    logger.warning("monitor_manager: failed to start %s: %s", key, exc)

    return started


def start_monitors_for_skill(skill_name: str) -> List[Dict[str, Any]]:
    """Start any ``when: on-skill-invoke:<skill_name>`` monitors."""
    if not skill_name:
        return []
    return start_plugin_monitors(
        when_values=(f"on-skill-invoke:{skill_name.strip().lower()}",)
    )


def list_monitors() -> List[Dict[str, Any]]:
    """List tracked monitors with a best-effort liveness flag."""
    try:
        from tools.process_registry import process_registry
    except Exception:  # noqa: BLE001
        process_registry = None  # type: ignore[assignment]

    out: List[Dict[str, Any]] = []
    with _lock:
        items = list(_started.items())
    for key, sid in items:
        alive = None
        if process_registry is not None:
            try:
                status = process_registry.poll(sid)
                alive = not bool(status.get("exited")) if isinstance(status, dict) else None
            except Exception:  # noqa: BLE001
                alive = None
        out.append({"key": key, "session_id": sid, "alive": alive})
    return out


def stop_all_monitors() -> int:
    """Kill all tracked monitor processes. Returns the count stopped."""
    try:
        from tools.process_registry import process_registry
    except Exception:  # noqa: BLE001
        return 0
    stopped = 0
    with _lock:
        items = list(_started.items())
        _started.clear()
    for _key, sid in items:
        try:
            process_registry.kill_process(sid)
            stopped += 1
        except Exception:  # noqa: BLE001
            pass
    return stopped


def reset_for_test() -> None:
    """Clear tracking state without touching processes (test helper)."""
    with _lock:
        _started.clear()
