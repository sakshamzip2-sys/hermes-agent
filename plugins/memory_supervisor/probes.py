"""Store liveness probes for the Runtime Memory Supervisor (RMS).

Each memory store gets a probe callable returning a :class:`ProbeResult`.  The
probes are INJECTABLE: the control loop is constructed with a ``probes`` mapping,
so tests pass fake up/down probes and exercise the full breaker + queue machinery
with no live servers.  The defaults wire to the real signals:

* ``honcho`` / ``gbrain`` -> reuse the same ``GET /health`` checks the existing
  ``gateway/platforms/memory_aggregator.py`` aggregator already performs, so the
  supervisor and the Memory tab agree on liveness.
* ``local`` (FTS5 session store) / ``holographic`` (local fact store) -> a
  trivially-up probe.  These are local SQLite planes that commit synchronously;
  they are only DOWN if their file is unwritable, which the probe checks cheaply.

Every probe carries a status_code when it can (so failure classification can tell
a 402-credits problem from a transient 5xx) and never raises: a probe that throws
is caught by the control loop and counted as a failure (the loop's per-tick
try/except is the backstop).  Each probe is additionally run by the loop under a
HARD wall-clock deadline via ``run_probe_with_deadline`` so a socket stall that
ignores the httpx timeout cannot wedge the single loop thread.

No em dashes in emitted text (house rule).
"""

from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

try:  # httpx is a hard dependency of the agent
    import httpx
except Exception:  # pragma: no cover - defensive
    httpx = None  # type: ignore

# Canonical store ids the supervisor tracks.
STORE_LOCAL = "local"
STORE_HOLOGRAPHIC = "holographic"
STORE_HONCHO = "honcho"
STORE_GBRAIN = "gbrain"
DEFAULT_STORES = (STORE_LOCAL, STORE_HOLOGRAPHIC, STORE_HONCHO, STORE_GBRAIN)


@dataclass
class ProbeResult:
    """Outcome of a single store probe."""

    ok: bool
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    error: Optional[str] = None


# A probe is a zero-arg callable returning a ProbeResult.
Probe = Callable[[], ProbeResult]


def run_probe_with_deadline(probe: Probe, *, deadline_s: float) -> ProbeResult:
    """Run *probe* under a HARD wall-clock deadline.

    A probe that hangs (socket-level stall ignoring the client timeout) is
    abandoned and reported as a timeout failure, so the single loop thread is
    never wedged by one dead store.  The abandoned worker thread is a daemon and
    cannot block process exit.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        fut = pool.submit(_safe_probe, probe)
        try:
            return fut.result(timeout=deadline_s)
        except concurrent.futures.TimeoutError:
            return ProbeResult(ok=False, error=f"probe exceeded hard deadline {deadline_s}s")
    finally:
        # Do not wait on a hung worker; let the daemon pool be GC'd.
        pool.shutdown(wait=False)


def _safe_probe(probe: Probe) -> ProbeResult:
    try:
        return probe()
    except Exception as e:  # never let a probe raise into the loop
        return ProbeResult(ok=False, error=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Default probe implementations
# --------------------------------------------------------------------------- #

def _local_writable_probe(db_path_getter: Callable[[], Path]) -> ProbeResult:
    """A local SQLite plane is 'up' iff its directory is writable.  We do not
    open the DB (that would contend with the live writer); a writable parent dir
    is the cheap liveness signal that distinguishes a healthy local plane from a
    read-only/full filesystem."""
    try:
        path = db_path_getter()
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        if os.access(parent, os.W_OK):
            return ProbeResult(ok=True)
        return ProbeResult(ok=False, error=f"not writable: {parent}")
    except Exception as e:
        return ProbeResult(ok=False, error=f"{type(e).__name__}: {e}")


def _http_health_probe(url: str, *, timeout_s: float) -> ProbeResult:
    """Probe an HTTP store via ``GET {url}`` (mirrors the aggregator).  Reports
    the status code so the breaker can classify a 402/401 as permanent."""
    if httpx is None:
        return ProbeResult(ok=False, error="httpx unavailable")
    try:
        with httpx.Client(timeout=timeout_s) as cx:
            r = cx.get(url)
            ok = r.status_code == 200
            return ProbeResult(ok=ok, status_code=r.status_code,
                               error=None if ok else f"health {r.status_code}")
    except Exception as e:
        return ProbeResult(ok=False, error=f"unreachable: {type(e).__name__}: {e}")


def _honcho_health_url() -> str:
    # Resolve the same base the aggregator uses, then hit /health.
    try:
        from gateway.platforms.memory_aggregator import _honcho_config  # type: ignore

        base = _honcho_config()["base_url"]
    except Exception:
        base = os.environ.get("HONCHO_BASE_URL", "").strip() or "http://localhost:8000"
    return f"{base.rstrip('/')}/health"


def _gbrain_health_url() -> str:
    try:
        from gateway.platforms.memory_aggregator import _gbrain_base  # type: ignore

        base = _gbrain_base()
    except Exception:
        base = (os.environ.get("GBRAIN_HTTP_URL", "").strip() or "http://127.0.0.1:3131").rstrip("/")
    return f"{base}/health"


def default_probes(*, timeout_s: float = 2.0) -> Dict[str, Probe]:
    """Build the default probe mapping for the four stores.

    Imports of the local store DB paths are best-effort + lazy so the plugin
    loads even if a store plugin is absent; a store whose path cannot be
    resolved falls back to a trivially-up probe (fail open: never disable a
    store we cannot even locate)."""

    def _local_path() -> Path:
        try:
            from hermes_constants import get_hermes_home

            return Path(get_hermes_home()) / "memory" / "fts.db"
        except Exception:
            return Path(os.path.expanduser("~/.hermes")) / "memory" / "fts.db"

    def _holo_path() -> Path:
        try:
            from hermes_constants import get_hermes_home

            return Path(get_hermes_home()) / "memory_store.db"
        except Exception:
            return Path(os.path.expanduser("~/.hermes")) / "memory_store.db"

    return {
        STORE_LOCAL: lambda: _local_writable_probe(_local_path),
        STORE_HOLOGRAPHIC: lambda: _local_writable_probe(_holo_path),
        STORE_HONCHO: lambda: _http_health_probe(_honcho_health_url(), timeout_s=timeout_s),
        STORE_GBRAIN: lambda: _http_health_probe(_gbrain_health_url(), timeout_s=timeout_s),
    }
