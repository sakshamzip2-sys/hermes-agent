"""Single-writer guard for the Runtime Memory Supervisor (RMS).

CLAUDE.md documents a real dual-gateway local stack (a ``:8642`` daemon plus a
launchd daemon) that can share one ``~/.hermes/mem_supervisor.db``.  WAL gives
concurrent readers but exactly one writer.  If both gateways drained the write
queue they would double-apply external writes and storm the store with
``SQLITE_BUSY``.  So only the LEASE HOLDER drains the queue and runs scheduled
jobs; every other hermes process probes and publishes health read-only.

Two layers, belt and suspenders:

1. An OS advisory lock (``fcntl.flock``) on a sidecar ``mem_supervisor.db.lock``
   file.  This is a hard kernel-level guard: a second process on the same host
   cannot take it while the first holds it.  ``flock`` is unavailable on native
   Windows; there we fall back to the DB lease alone.
2. A DB lease row (:func:`wal.try_acquire_lease`) keyed by a ``holder_token`` of
   boot-id + pid + random nonce, so PID reuse cannot impersonate a dead holder
   and a cross-host writer (a future networked DB) is still arbitrated.  The
   lease is renewed each tick and expires after ``3 * tick_interval``.

``Identity`` (boot-id + pid + nonce) is also the worker identity the job
reconcile uses instead of a bare ``os.kill`` check.

No em dashes in emitted text (house rule).
"""

from __future__ import annotations

import os
import platform
import threading
import uuid
from dataclasses import dataclass
from typing import Optional

from . import wal

try:  # POSIX advisory lock; absent on native Windows.
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - windows
    fcntl = None  # type: ignore


def _boot_id() -> str:
    """A best-effort, stable-per-boot identifier so a reused PID after a reboot
    cannot be mistaken for the original holder.  Falls back to the node name +
    boot time when ``/proc`` is unavailable (macOS), and to a process-lifetime
    random when even that fails (still unique per process)."""
    # Linux: /proc/sys/kernel/random/boot_id is the canonical per-boot UUID.
    try:
        with open("/proc/sys/kernel/random/boot_id", "r", encoding="utf-8") as fh:
            val = fh.read().strip()
            if val:
                return val
    except Exception:
        pass
    # macOS / BSD: derive from kern.boottime via uptime is awkward; use the
    # node name plus the monotonic-since-epoch boot estimate from psutil if
    # present, else a per-process random (unique enough for impersonation guard).
    try:
        import psutil  # type: ignore

        return f"{platform.node()}:{int(psutil.boot_time())}"
    except Exception:
        return f"{platform.node()}:{uuid.uuid4().hex}"


@dataclass(frozen=True)
class Identity:
    """A process identity that survives PID reuse: boot-id + pid + nonce."""

    boot_id: str
    pid: int
    nonce: str

    @property
    def token(self) -> str:
        return f"{self.boot_id}|{self.pid}|{self.nonce}"

    @staticmethod
    def current() -> "Identity":
        return Identity(boot_id=_boot_id(), pid=os.getpid(), nonce=uuid.uuid4().hex[:12])


class SingleWriterLease:
    """Holds (or fails to hold) the single-writer lease.

    Usage in the control loop::

        lease = SingleWriterLease(identity, lease_s=3 * tick_interval)
        if lease.acquire():
            # leader: drain queue, run scheduled jobs
        else:
            # follower: publish health read-only only
        ...
        lease.release()   # on shutdown
    """

    def __init__(self, identity: Optional[Identity] = None, *, lease_s: float = 30.0) -> None:
        self.identity = identity or Identity.current()
        self.lease_s = float(lease_s)
        self._lock = threading.Lock()
        self._flock_fh = None  # type: ignore[var-annotated]
        self._has_flock = False
        self._is_leader = False

    @property
    def token(self) -> str:
        return self.identity.token

    @property
    def is_leader(self) -> bool:
        with self._lock:
            return self._is_leader

    def _try_flock(self) -> bool:
        """Take the OS advisory lock if not already held.  Returns True if this
        process holds it (or flock is unavailable, in which case the DB lease is
        the sole arbiter)."""
        if fcntl is None:
            return True  # windows: rely on the DB lease alone
        if self._has_flock:
            return True
        try:
            path = str(wal.lock_path())
            fh = open(path, "a+")
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                fh.close()
                return False
            self._flock_fh = fh
            self._has_flock = True
            return True
        except Exception:
            # If we cannot even open the lock file, defer to the DB lease so we
            # never crash the loop on a lock-file problem.
            return True

    def acquire(self, *, now: Optional[float] = None) -> bool:
        """Attempt to (re)acquire leadership.  Idempotent: a current holder just
        renews.  Returns True iff this process is the leader after the call."""
        with self._lock:
            have_flock = self._try_flock()
            if not have_flock:
                self._is_leader = False
                return False
            got = wal.try_acquire_lease(
                holder_token=self.identity.token, lease_s=self.lease_s, now=now
            )
            self._is_leader = bool(got)
            return self._is_leader

    def release(self) -> None:
        with self._lock:
            try:
                if self._is_leader:
                    wal.release_lease(holder_token=self.identity.token)
            except Exception:
                pass
            self._is_leader = False
            if self._flock_fh is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(self._flock_fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    self._flock_fh.close()
                except Exception:
                    pass
                self._flock_fh = None
            self._has_flock = False
