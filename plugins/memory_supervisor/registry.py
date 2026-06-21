"""Process-singleton holder + gateway start hook for the Memory Supervisor.

Mirrors ``gateway/memory_monitor.py``'s module-level singleton + ``_lock`` +
idempotent ``start_*`` discipline, so the gateway start path can call
``start_memory_supervisor()`` exactly like it calls ``start_memory_monitoring()``.

Default safe: the supervisor is OPT-IN (``memory_supervisor.enabled: false`` by
default).  When disabled or absent, ``start_memory_supervisor()`` returns False
and NOTHING changes: recall keeps fanning out to all stores and swallowing
failures (today's behavior), writes keep their best-effort inline path.

No em dashes in emitted text (house rule).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from .breaker import BreakerConfig
from .control_loop import MemorySupervisor, SupervisorConfig

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_supervisor: Optional[MemorySupervisor] = None


def get_memory_supervisor() -> Optional[MemorySupervisor]:
    """Return the running supervisor singleton, or None if not started.

    The recall fan-out / write path call this; a None return means 'plugin not
    active' and they fall back to today's behavior.
    """
    with _lock:
        return _supervisor


def _resolve_config() -> Dict[str, Any]:
    """Best-effort read of the ``memory_supervisor`` block from config.yaml.

    Never raises: a missing config means defaults (and disabled)."""
    try:
        from hermes_cli.config import load_config  # type: ignore

        cfg = load_config() or {}
    except Exception:
        return {}
    if isinstance(cfg, dict):
        block = cfg.get("memory_supervisor")
        return block if isinstance(block, dict) else {}
    block = getattr(cfg, "memory_supervisor", None)
    return block if isinstance(block, dict) else {}


def _build_config(block: Dict[str, Any]) -> SupervisorConfig:
    breaker_block = block.get("breaker", {}) if isinstance(block.get("breaker"), dict) else {}
    breaker = BreakerConfig(
        fail_threshold=int(breaker_block.get("fail_threshold", 3)),
        recover_successes=int(breaker_block.get("recover_successes", 2)),
        cooldown_s=float(breaker_block.get("cooldown_s", 30.0)),
        cooldown_max_s=float(breaker_block.get("cooldown_max_s", 300.0)),
        jitter_frac=float(breaker_block.get("jitter_frac", 0.2)),
    )
    probe_block = block.get("probe", {}) if isinstance(block.get("probe"), dict) else {}
    return SupervisorConfig(
        tick_interval_s=float(block.get("tick_interval_s", 10.0)),
        probe_timeout_s=float(probe_block.get("timeout_s", 2.0)),
        probe_hard_deadline_s=float(probe_block.get("hard_deadline_s", 5.0)),
        breaker=breaker,
    )


def start_memory_supervisor(*, force: bool = False) -> bool:
    """Start the Runtime Memory Supervisor daemon thread.

    Idempotent: a second call while one is running returns False.  Returns True
    only when a fresh supervisor was started.  Disabled-by-config returns False
    (and is the default), changing nothing.

    ``force=True`` bypasses the config gate (used by tests / explicit opt-in).
    """
    global _supervisor
    block = _resolve_config()
    enabled = bool(block.get("enabled", False)) or force
    if not enabled:
        logger.debug("[MEM-SUP] memory_supervisor.enabled is false; not starting.")
        return False

    with _lock:
        if _supervisor is not None and _supervisor.is_running():
            return False
        try:
            config = _build_config(block)
            sup = MemorySupervisor(config=config)
        except Exception as e:
            logger.warning("[MEM-SUP] failed to construct supervisor: %s", e)
            return False
        started = sup.start()
        if started:
            _supervisor = sup
        return started


def stop_memory_supervisor(timeout: float = 2.0) -> None:
    """Stop the supervisor if running.  Safe to call when never started."""
    global _supervisor
    with _lock:
        sup = _supervisor
        _supervisor = None
    if sup is not None:
        try:
            sup.stop(timeout=timeout)
        except Exception:
            pass


def is_running() -> bool:
    with _lock:
        return _supervisor is not None and _supervisor.is_running()
