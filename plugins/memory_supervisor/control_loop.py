"""The Runtime Memory Supervisor (RMS) control loop and public API.

A SECOND daemon thread next to ``gateway/memory_monitor.py``'s RSS monitor.  It
mirrors that module's discipline exactly: ``daemon=True`` so it never blocks
process exit, a module ``_lock`` + idempotent start, and a per-tick
``try/except`` so one bad probe never kills the loop (no cascade).  Absence of
this plugin, or a loop that never starts, changes nothing: recall falls back to
today's fan-out-to-all behavior, writes fall back to best-effort inline.

Each tick (leader only does the mutating work; a non-leader publishes health
read-only):

1. Heartbeat FIRST (``record_tick``) so the watchdog can tell a live loop from a
   wedged one.
2. Probe every store under a HARD wall-clock deadline; feed the breaker; mirror
   the breaker state into ``store_health`` (the visible signal that fixes silent
   degradation).
3. Reconcile stuck/dead background jobs (wall-clock heartbeat, not bare pid).
4. Reclaim stale inflight writes, then drain the durable write queue for each
   store whose breaker is CLOSED/HALF_OPEN, with capped exponential backoff +
   full jitter on failure; terminal failures dead-letter.

Public API:

* ``enqueue_write(store, payload)`` — FAIL-CLOSED durable enqueue (the seam the
  memory write path calls).  A write to a down store is QUEUED, never dropped.
* ``recall_allowed(store)`` — FAIL-OPEN gate the recall fan-out consults to skip
  an OPEN store fast (no per-turn timeout).
* ``get_health()`` — the agent-visible / aggregator-readable health view.

No em dashes in emitted text (house rule).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import wal
from .breaker import BreakerConfig, BreakerRegistry, BreakerState, classify_failure, FailureClass
from .lease import Identity, SingleWriterLease
from .probes import DEFAULT_STORES, Probe, ProbeResult, default_probes, run_probe_with_deadline

logger = logging.getLogger(__name__)


# A drainer takes a write_queue row dict and returns (ok, status_code, error).
# It is injectable so tests simulate a store that is up/down without a real
# provider, and so the real wiring (calling the memory provider) stays out of
# this module's import graph.
Drainer = Callable[[Dict[str, Any]], Tuple[bool, Optional[int], Optional[str]]]


@dataclass
class SupervisorConfig:
    """Loop tunables (defaults mirror PHASE3 section 2.10)."""

    tick_interval_s: float = 10.0
    probe_timeout_s: float = 2.0
    probe_hard_deadline_s: float = 5.0
    breaker: BreakerConfig = field(default_factory=BreakerConfig)
    job_stuck_factor: float = 3.0        # job dead if no heartbeat for factor*tick
    backoff_base_s: float = 2.0
    backoff_jitter_frac: float = 0.2
    write_max_attempts: int = 5
    max_queue_depth_per_store: int = 5000
    drain_batch_per_store: int = 50      # token-bucket-ish cap per tick
    lease_factor: float = 3.0            # lease/heartbeat staleness = factor*tick
    watchdog_factor: float = 3.0         # heartbeat stale if older than factor*tick


class MemorySupervisor:
    """Owns the breaker registry, the lease, and the control-loop thread.

    Construct with injectable ``probes`` and ``drainer`` so the whole machine is
    testable with no live servers.  In production the start hook builds it with
    the real defaults.
    """

    def __init__(
        self,
        config: Optional[SupervisorConfig] = None,
        *,
        stores: Optional[List[str]] = None,
        probes: Optional[Dict[str, Probe]] = None,
        drainer: Optional[Drainer] = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.config = config or SupervisorConfig()
        self.stores = list(stores or DEFAULT_STORES)
        self._probes = probes or default_probes(timeout_s=self.config.probe_timeout_s)
        self._drainer = drainer  # None -> queue only, never drain (still fail-closed)
        self._wall = wall_clock
        self._rng = rng or random.Random()
        self.breakers = BreakerRegistry(
            self.config.breaker, clock=clock, wall_clock=wall_clock, rng=self._rng
        )
        self.identity = Identity.current()
        self.lease = SingleWriterLease(
            self.identity, lease_s=self.config.lease_factor * self.config.tick_interval_s
        )
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._restore_breakers_from_health()

    # ------------------------------------------------------------------ #
    # Thread lifecycle (mirrors memory_monitor.start/stop)
    # ------------------------------------------------------------------ #
    def start(self) -> bool:
        """Start the loop thread if not already running.  Returns True on a fresh
        start, False if already running.  Idempotent."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop = threading.Event()
            # An immediate tick so health is populated before the first interval.
            try:
                self._tick()
            except Exception as e:  # pragma: no cover - defensive
                logger.debug("[MEM-SUP] initial tick failed: %s", e)
            self._thread = threading.Thread(
                target=self._loop, name="memory-supervisor", daemon=True
            )
            self._thread.start()
            logger.info(
                "[MEM-SUP] Runtime Memory Supervisor started (tick=%ds, stores=%s)",
                int(self.config.tick_interval_s), ",".join(self.stores),
            )
            return True

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
            self._stop.set()
        # Always release the lease, even if no loop thread was running: a direct
        # tick (or the initial tick in start()) can hold leadership.
        try:
            self.lease.release()
        except Exception:
            pass
        if thread is not None:
            try:
                thread.join(timeout=timeout)
            except Exception:
                pass
        logger.info("[MEM-SUP] Runtime Memory Supervisor stopped")

    def is_running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        interval = self.config.tick_interval_s
        while not self._stop.wait(interval):
            try:
                self._tick()
            except Exception as e:
                # NEVER let the loop die: log and carry on (no cascade to the
                # agent turn).  This is the backstop the watchdog complements.
                logger.warning("[MEM-SUP] tick failed (continuing): %s", e)

    # ------------------------------------------------------------------ #
    # One tick
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        now = self._wall()
        # 1) Heartbeat FIRST (the watchdog signal).
        try:
            wal.record_tick(boot_id=self.identity.boot_id, pid=self.identity.pid, now=now)
        except Exception as e:
            logger.debug("[MEM-SUP] record_tick failed: %s", e)

        # 2) Probe + breaker + publish health (every process does this).
        for store in self.stores:
            self._probe_and_publish(store, now=now)

        is_leader = self._acquire_leadership(now=now)
        if not is_leader:
            return  # followers publish health only; the leader does mutating work

        # 3) Reconcile stuck/dead jobs.
        try:
            stuck_after = self.config.job_stuck_factor * self.config.tick_interval_s
            wal.reconcile_jobs(stuck_after_s=stuck_after, now=now)
        except Exception as e:
            logger.debug("[MEM-SUP] reconcile_jobs failed: %s", e)

        # 4) Reclaim stale inflight writes, then drain.
        try:
            wal.reclaim_stale_inflight(now=now)
        except Exception as e:
            logger.debug("[MEM-SUP] reclaim_stale_inflight failed: %s", e)
        self._drain_all(now=now)

    def _acquire_leadership(self, *, now: float) -> bool:
        try:
            return self.lease.acquire(now=now)
        except Exception as e:
            logger.debug("[MEM-SUP] lease acquire failed: %s", e)
            return False

    def _probe_and_publish(self, store: str, *, now: float) -> None:
        breaker = self.breakers.get(store)
        probe = self._probes.get(store)
        # CLOSED stores get a cheap probe; OPEN/HALF_OPEN need the gated probe to
        # drive recovery.  If the breaker says skip and it is not yet time to
        # half-open, we still publish the (skipped) state so it is visible.
        allow = breaker.allow_request()
        if probe is None:
            # No probe for this store: treat as up (fail open) and publish.
            self._publish_health(store, breaker, now=now, probed=False)
            return
        if not allow and breaker.state in (BreakerState.OPEN, BreakerState.CREDITS_EXHAUSTED, BreakerState.SUPERVISOR_DEGRADED):
            # Skip the call fast, just re-publish the current (visible) state.
            self._publish_health(store, breaker, now=now, probed=False)
            return
        result = run_probe_with_deadline(probe, deadline_s=self.config.probe_hard_deadline_s)
        if result.ok:
            breaker.record_success()
        else:
            breaker.record_failure(status_code=result.status_code, reason=result.error)
        self._publish_health(store, breaker, now=now, probed=True, last_error=result.error)

    def _publish_health(
        self, store: str, breaker, *, now: float, probed: bool, last_error: Optional[str] = None
    ) -> None:
        snap = breaker.snapshot()
        try:
            wal.upsert_store_health(
                store=store,
                state=snap.state,
                consecutive_failures=snap.consecutive_failures,
                last_probe_at=now if probed else None,
                last_change_at=snap.last_change_at,
                last_error=last_error if last_error is not None else snap.last_failure_reason,
            )
        except Exception as e:
            logger.debug("[MEM-SUP] publish health for %s failed: %s", store, e)

    # ------------------------------------------------------------------ #
    # Write queue draining (fail-closed)
    # ------------------------------------------------------------------ #
    def _drain_all(self, *, now: float) -> None:
        if self._drainer is None:
            return  # no provider wired: writes still durably queued, just not drained
        for store in self.stores:
            breaker = self.breakers.get(store)
            if breaker.is_open():
                continue  # OPEN/credits/degraded: leave queued, drain on recovery
            self._drain_store(store, breaker, now=now)

    def _drain_store(self, store: str, breaker, *, now: float) -> None:
        lease_token = self.identity.token
        lease_s = self.config.lease_factor * self.config.tick_interval_s
        drained = 0
        while drained < self.config.drain_batch_per_store:
            if breaker.is_open():
                break
            try:
                row = wal.claim_next_write(store, lease_token=lease_token, lease_s=lease_s, now=self._wall())
            except Exception as e:
                logger.debug("[MEM-SUP] claim_next_write(%s) failed: %s", store, e)
                break
            if row is None:
                break
            ok, status_code, error = self._call_drainer(row)
            if ok:
                breaker.record_success()
                try:
                    wal.ack_write(int(row["id"]))
                except Exception as e:
                    logger.debug("[MEM-SUP] ack_write failed: %s", e)
            else:
                cls = classify_failure(status_code)
                permanent = cls is FailureClass.PERMANENT
                breaker.record_failure(status_code=status_code, reason=error)
                backoff_at = self._backoff_at(int(row["attempts"]))
                try:
                    wal.fail_write(int(row["id"]), error=error or "drain failed",
                                   backoff_at=backoff_at, permanent=permanent)
                except Exception as e:
                    logger.debug("[MEM-SUP] fail_write failed: %s", e)
                # On a failure the store is suspect; stop draining it this tick.
                break
            drained += 1

    def _call_drainer(self, row: Dict[str, Any]):
        assert self._drainer is not None
        try:
            return self._drainer(row)
        except Exception as e:  # a drainer that raises is a transient failure
            return (False, None, f"{type(e).__name__}: {e}")

    def _backoff_at(self, attempts: int) -> float:
        base = self.config.backoff_base_s * (2 ** max(0, attempts))
        jitter = base * self.config.backoff_jitter_frac
        delay = base + self._rng.uniform(0.0, jitter)  # full (one-sided) jitter
        return self._wall() + max(0.0, delay)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def enqueue_write(self, store: str, payload: Any, *, op: str = "write") -> str:
        """FAIL-CLOSED durable enqueue.  Caps the queue depth (oldest overflow
        dead-letters, never silently dropped) and journals the write.  Returns
        the stable dedup_key (idempotent: re-enqueuing the same logical write is
        a no-op)."""
        try:
            wal.evict_oldest_over_cap(store, self.config.max_queue_depth_per_store)
        except Exception as e:
            logger.debug("[MEM-SUP] evict_oldest_over_cap failed: %s", e)
        return wal.enqueue_write(store, payload, op=op, max_attempts=self.config.write_max_attempts)

    def recall_allowed(self, store: str) -> bool:
        """FAIL-OPEN recall gate.  True iff the recall fan-out should query
        *store*.  An OPEN/credits/degraded store returns False so it is skipped
        fast; an unknown store returns True (never block a turn)."""
        breaker = self.breakers.get(store)
        return not breaker.is_open()

    def get_health(self) -> Dict[str, Any]:
        """Agent-visible / aggregator-readable health view.  Reads the durable
        ``store_health`` + ``supervisor_status`` rows so it is correct even after
        a restart, before the first fresh tick."""
        out: Dict[str, Any] = {"enabled": True, "stores": {}, "supervisor": {}}
        try:
            for row in wal.get_store_health():
                out["stores"][row["store"]] = {
                    "state": row["state"],
                    "consecutive_failures": row["consecutive_failures"],
                    "last_probe_at": row["last_probe_at"],
                    "last_change_at": row["last_change_at"],
                    "last_error": row["last_error"],
                    "down": row["state"] not in ("closed", "half_open"),
                }
        except Exception as e:
            out["error"] = f"store_health read failed: {e}"
        try:
            st = wal.get_status() or {}
            watchdog_age = self.config.watchdog_factor * self.config.tick_interval_s
            out["supervisor"] = {
                "last_tick_at": st.get("last_tick_at"),
                "tick_count": st.get("tick_count"),
                "is_leader": self.lease.is_leader,
                "running": self.is_running(),
                "heartbeat_stale": wal.heartbeat_stale(max_age_s=watchdog_age, now=self._wall()),
                "pending_writes": wal.queue_depth(),
                "dead_letter_writes": wal.dead_letter_count(),
            }
        except Exception as e:
            out["supervisor"]["error"] = f"status read failed: {e}"
        return out

    # ------------------------------------------------------------------ #
    # Watchdog hook (called by the RSS monitor on its own timer)
    # ------------------------------------------------------------------ #
    def watchdog_check(self) -> bool:
        """Return True if the loop heartbeat is FRESH; if stale, log LOUD, flip
        all breakers to ``supervisor_degraded``, and restart the loop thread.

        Mirrors the existing RSS-monitor pattern: a cheap second check on its own
        timer that does not trust ``is_alive()`` as liveness."""
        try:
            max_age = self.config.watchdog_factor * self.config.tick_interval_s
            if not wal.heartbeat_stale(max_age_s=max_age, now=self._wall()):
                return True
        except Exception as e:
            logger.debug("[MEM-SUP] watchdog heartbeat check failed: %s", e)
            return True  # cannot tell -> do not thrash
        logger.warning(
            "[MEM-SUP] WATCHDOG: supervisor heartbeat stale (> %ss). Restarting loop.",
            self.config.watchdog_factor * self.config.tick_interval_s,
        )
        try:
            self.breakers.force_all(BreakerState.SUPERVISOR_DEGRADED, reason="watchdog: stale heartbeat")
        except Exception:
            pass
        try:
            self.stop(timeout=0.5)
        except Exception:
            pass
        try:
            self.start()
        except Exception as e:
            logger.warning("[MEM-SUP] watchdog restart failed: %s", e)
        return False

    # ------------------------------------------------------------------ #
    # Cold-start breaker restore (resumability)
    # ------------------------------------------------------------------ #
    def _restore_breakers_from_health(self) -> None:
        try:
            for row in wal.get_store_health():
                breaker = self.breakers.get(row["store"])
                breaker.restore(
                    state=row["state"],
                    consecutive_failures=int(row["consecutive_failures"] or 0),
                )
        except Exception as e:
            logger.debug("[MEM-SUP] restore breakers failed: %s", e)
