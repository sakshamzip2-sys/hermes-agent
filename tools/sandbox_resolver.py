"""Resolve the ``auto`` terminal backend to a concrete, isolation-preferring choice.

``terminal.backend`` (env ``TERMINAL_ENV``) selects where agent-generated shell
and ``execute_code`` actually run.  Historically the default was ``local`` — the
agent's untrusted, model-authored code executed directly on the host with no
kernel isolation.  The default is now ``auto``: prefer a kernel-isolated backend
when one is available on the machine, and fall back to ``local`` (today's
behavior) when none is, logging the downgrade so it is never silent.

This module is intentionally dependency-light (stdlib only) and imports neither
``hermes_cli.config`` nor the heavy parts of ``tools.terminal_tool`` so that both
the config→env bridge and the terminal execution seam can call it without a
circular import.

Resolution policy for ``auto`` (first match wins):

1. ``docker``  — a running Docker daemon (``docker info`` succeeds).  Self-hostable,
   local, ~hundreds-of-ms cold start; the v2 Docker backend is hardened
   (``--cap-drop ALL``, ``--no-new-privileges``, pid/cpu/mem limits).
2. ``modal``   — the ``modal`` SDK is importable AND a token is configured
   (microVM-style ``Sandbox``).
3. ``local``   — no isolated backend available → host execution (logged WARNING).

The probe runs at most once per process (cached); the resolved value is written
back to ``os.environ['TERMINAL_ENV']`` so every downstream reader that compares
the raw env var against ``"docker"``/``"local"`` sees a concrete backend and
never has to understand ``"auto"``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Backends that provide real kernel/VM isolation (mirrors the set the approval
# gate treats as already-sandboxed in tools/approval.py).
ISOLATED_BACKENDS = frozenset({"docker", "singularity", "modal", "daytona"})

# How long to wait on the ``docker info`` daemon probe before giving up and
# falling back.  A stopped daemon must not hang agent startup.
_DOCKER_PROBE_TIMEOUT_S = 5

_CACHE_LOCK = threading.Lock()
# Cached concrete result of resolving ``auto`` for this process; None = not yet
# probed.  Keyed only on "auto" since explicit backends never hit the cache.
# The probed value is a machine-global fact (Docker present or not), so it is
# safe to share across the in-process subagent thread pool.
_auto_choice: Optional[str] = None


def _probe_docker_available() -> bool:
    """Return True when a usable Docker daemon is reachable.

    ``shutil.which`` short-circuits instantly on machines with no Docker CLI, so
    the (slower) ``docker info`` daemon check only runs when the binary exists.
    """
    docker_exe = shutil.which("docker")
    if not docker_exe:
        return False
    try:
        result = subprocess.run(
            [docker_exe, "info"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_DOCKER_PROBE_TIMEOUT_S,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        # Daemon down, permission denied, timeout — treat as unavailable.
        return False


def _probe_modal_configured() -> bool:
    """Return True when Modal can plausibly run a sandbox (SDK + token present)."""
    import importlib.util

    try:
        if importlib.util.find_spec("modal") is None:
            return False
    except Exception:
        return False
    if os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"):
        return True
    return os.path.exists(os.path.expanduser("~/.modal.toml"))


def _resolve_auto(force: bool = False) -> str:
    """Probe for an isolated backend; cache and return the concrete choice."""
    global _auto_choice
    with _CACHE_LOCK:
        if _auto_choice is not None and not force:
            return _auto_choice

        if _probe_docker_available():
            choice = "docker"
        elif _probe_modal_configured():
            choice = "modal"
        else:
            choice = "local"

        if choice == "local":
            logger.warning(
                "TERMINAL_ENV=auto: no isolated execution backend available "
                "(no running Docker daemon, Modal not configured) — falling back "
                "to LOCAL host execution. Agent-generated code will run on the "
                "host with no kernel isolation. Install Docker (or configure "
                "Modal) to get sandboxed-by-default code execution."
            )
        else:
            logger.info(
                "TERMINAL_ENV=auto resolved to isolated backend %r.", choice
            )
        _auto_choice = choice
        return choice


def resolve_terminal_backend(
    raw: Optional[str] = None,
    *,
    write_back: bool = True,
) -> "tuple[str, bool]":
    """Resolve a (possibly ``auto``) backend name to a concrete backend.

    Args:
        raw: The configured backend.  When None, read ``TERMINAL_ENV`` (default
            ``auto``).
        write_back: When True and ``raw`` is ``auto``, store the resolved concrete
            value into ``os.environ['TERMINAL_ENV']`` so later literal readers see
            it.  Disable in tests / read-only probes that must not mutate env.

    Returns:
        ``(backend, was_auto)`` where ``backend`` is one of
        ``local``/``docker``/``singularity``/``modal``/``daytona``/``ssh`` (never
        ``auto``) and ``was_auto`` is True when the backend came from resolving
        ``auto``.  Returning ``was_auto`` per-call (rather than via a shared
        global) keeps the result correct under the in-process subagent thread
        pool, where many resolutions interleave.
    """
    if raw is None:
        raw = os.getenv("TERMINAL_ENV", "auto")
    raw = (raw or "auto").strip().lower() or "auto"

    if raw != "auto":
        return raw, False

    choice = _resolve_auto()
    if write_back:
        # Concretize the env var so credential gating, the gateway, doctor, etc.
        # all observe a real backend instead of "auto".  The probed value is a
        # machine-global fact, so writing it from any thread is deterministic.
        os.environ["TERMINAL_ENV"] = choice
    return choice, True


def resolve_backend_name(raw: Optional[str] = None, *, write_back: bool = True) -> str:
    """Convenience wrapper returning just the concrete backend name."""
    return resolve_terminal_backend(raw, write_back=write_back)[0]


def reset_cache() -> None:
    """Clear the cached probe result (tests only)."""
    global _auto_choice
    with _CACHE_LOCK:
        _auto_choice = None
