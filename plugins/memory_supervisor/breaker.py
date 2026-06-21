"""Per-store circuit breaker for the Runtime Memory Supervisor (RMS).

The breaker is the mechanism that makes recall FAIL OPEN cheaply: when a store
(Honcho / GBrain / local FTS5 / holographic) is down, today the recall fan-out
still dispatches to it and eats the full client timeout (the aggregator's 8s
``_TIMEOUT``).  A breaker in the OPEN state answers ``allow_request() -> False``
in O(1) with no network call, so the fan-out SKIPS the dead store instead of
awaiting it.  See PHASE3-orchestration-spec.md section 2.7.

State machine (debounced, hysteresis on both edges)::

    CLOSED  --(K consecutive failures)-->  OPEN
    OPEN    --(cooldown elapsed)--------->  HALF_OPEN
    HALF_OPEN --(M consecutive successes)-> CLOSED
    HALF_OPEN --(any failure)------------>  OPEN   (cooldown bumped)

Extra states from the spec:

* ``credits_exhausted`` — a permanent-class failure (HTTP 402 / no credits).
  It does NOT flap the breaker and does NOT burn the retry ladder; the store is
  parked in a distinct health state and cleared only by an operator.  We model
  it here as a terminal-until-reset breaker state that ``allow_request`` treats
  like OPEN (skip fast) so an embedding-dependent call short-circuits, while the
  control loop keeps lexical/FTS5 recall serving (that policy lives in the loop,
  not the breaker).
* ``supervisor_degraded`` — set by the watchdog when the loop heartbeat is
  stale; treated like OPEN for ``allow_request`` purposes.

The breaker is a pure in-memory object with no I/O of its own; the control loop
mirrors its ``snapshot()`` into ``store_health`` for resumability and reads
``store_health`` on cold start to restore state.  All timing uses a monotonic
clock so a wall-clock jump cannot wedge a cooldown.

No em dashes in user-facing strings (house rule); this module's docstrings use
them only in prose, never in emitted text.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional


class BreakerState(str, Enum):
    """Circuit-breaker states.  ``str`` mixin so values serialize cleanly to
    the ``store_health`` table and JSON without extra conversion."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
    CREDITS_EXHAUSTED = "credits_exhausted"
    SUPERVISOR_DEGRADED = "supervisor_degraded"


class FailureClass(str, Enum):
    """Coarse classification of a failure so the breaker can react correctly.

    * ``TRANSIENT`` — 429 / 5xx / timeout / connection error: normal breaker
      flapping + retry-ladder eligible.
    * ``PERMANENT`` — 402 / 401 / 403 / 400: a config/credits problem retrying
      cannot fix.  402 specifically routes to ``CREDITS_EXHAUSTED`` and never
      burns the retry ladder.
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"


# HTTP status codes that classification treats as PERMANENT (not worth retry).
_PERMANENT_CODES = frozenset({400, 401, 402, 403})
# The single code that means "out of credits" -> credits_exhausted park.
_CREDITS_CODE = 402


def classify_failure(
    status_code: Optional[int] = None,
    *,
    exc: Optional[BaseException] = None,
) -> FailureClass:
    """Classify a failure as transient or permanent from an HTTP status and/or
    exception.

    A bare timeout/connection error (no status code) is TRANSIENT.  Only an
    explicit permanent status code (400/401/402/403) is PERMANENT.  This keeps
    a momentary network blip from being mistaken for a credits/auth problem.
    """
    if status_code is not None and int(status_code) in _PERMANENT_CODES:
        return FailureClass.PERMANENT
    # Exceptions are always transient here: a TimeoutError, ConnectionError, or
    # any client exception means "could not reach / complete", which is the
    # retry-eligible class.  Permanent failures arrive as a real status code.
    return FailureClass.TRANSIENT


def is_credits_code(status_code: Optional[int]) -> bool:
    """True iff *status_code* is the 'insufficient credits' signal (402)."""
    return status_code is not None and int(status_code) == _CREDITS_CODE


@dataclass
class BreakerConfig:
    """Tunables for one breaker.  Defaults mirror PHASE3 section 2.10."""

    fail_threshold: int = 3       # K consecutive failures: CLOSED -> OPEN
    recover_successes: int = 2    # M consecutive successes: HALF_OPEN -> CLOSED
    cooldown_s: float = 30.0      # base OPEN cooldown before HALF_OPEN
    cooldown_max_s: float = 300.0  # cap on the exponential cooldown
    jitter_frac: float = 0.2      # +/- jitter on the cooldown so stores de-sync


@dataclass
class _Counters:
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    total_failures: int = 0
    total_successes: int = 0
    trips: int = 0  # how many times we entered OPEN (for cooldown backoff)


@dataclass
class BreakerSnapshot:
    """Serializable view of a breaker for ``store_health`` / health APIs."""

    store: str
    state: str
    consecutive_failures: int
    consecutive_successes: int
    total_failures: int
    total_successes: int
    open_until_monotonic: Optional[float]
    last_change_at: float          # wall-clock epoch seconds
    last_failure_reason: Optional[str] = None


class CircuitBreaker:
    """A thread-safe per-store circuit breaker.

    Usage from the recall fan-out (fail open)::

        if breaker.allow_request():
            try:
                result = call_store(...)
                breaker.record_success()
            except Exception as e:
                breaker.record_failure(exc=e)
        else:
            # OPEN: skip this store FAST, no network, no per-turn timeout.
            ...

    The monotonic clock is injectable (``clock=``) so tests can drive cooldown
    transitions deterministically without sleeping.
    """

    def __init__(
        self,
        store: str,
        config: Optional[BreakerConfig] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.store = store
        self.config = config or BreakerConfig()
        self._clock = clock
        self._wall = wall_clock
        self._rng = rng or random.Random()
        self._lock = threading.RLock()
        self._state: BreakerState = BreakerState.CLOSED
        self._counters = _Counters()
        # Monotonic deadline after which an OPEN breaker may probe (HALF_OPEN).
        self._open_until: Optional[float] = None
        self._last_change_at: float = self._wall()
        self._last_failure_reason: Optional[str] = None
        # Whether the single gated HALF_OPEN probe slot is currently claimed.
        self._half_open_probe_inflight: bool = False

    # ------------------------------------------------------------------ #
    # State inspection
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> BreakerState:
        """Return the breaker's logical state, applying a lazy OPEN -> HALF_OPEN
        transition if the cooldown has elapsed.  Reading the state can advance
        it (this is intentional and standard for circuit breakers)."""
        with self._lock:
            self._maybe_half_open_locked()
            return self._state

    def is_open(self) -> bool:
        """True iff calls should be short-circuited (OPEN / credits / degraded).

        A breaker in HALF_OPEN is NOT 'open' for this purpose: HALF_OPEN allows
        exactly one gated probe through.
        """
        return not self.allow_request(consume=False)

    def allow_request(self, *, consume: bool = True) -> bool:
        """Return True if a call may proceed.

        * CLOSED -> always True.
        * OPEN -> False until the cooldown elapses, then becomes HALF_OPEN.
        * HALF_OPEN -> True for the FIRST caller (the gated probe) and False for
          concurrent callers, so only one probe is in flight at a time.  Set
          ``consume=False`` to peek without claiming the probe slot.
        * CREDITS_EXHAUSTED / SUPERVISOR_DEGRADED -> False (skip fast).
        """
        with self._lock:
            self._maybe_half_open_locked()
            if self._state is BreakerState.CLOSED:
                return True
            if self._state is BreakerState.HALF_OPEN:
                if not consume:
                    return True
                # Claim the single probe slot: flip to a marker that blocks
                # further concurrent probes until success/failure resolves it.
                if self._half_open_probe_inflight:
                    return False
                self._half_open_probe_inflight = True
                return True
            # OPEN, CREDITS_EXHAUSTED, SUPERVISOR_DEGRADED -> skip fast.
            return False

    # ------------------------------------------------------------------ #
    # Outcome recording
    # ------------------------------------------------------------------ #
    def record_success(self) -> BreakerState:
        """Record a successful probe/call and advance the state machine."""
        with self._lock:
            self._half_open_probe_inflight = False
            c = self._counters
            c.total_successes += 1
            c.consecutive_successes += 1
            c.consecutive_failures = 0

            if self._state is BreakerState.HALF_OPEN:
                if c.consecutive_successes >= self.config.recover_successes:
                    self._transition_locked(BreakerState.CLOSED)
            elif self._state in (
                BreakerState.OPEN,
                BreakerState.SUPERVISOR_DEGRADED,
            ):
                # A success arriving while OPEN (e.g. an out-of-band probe) is
                # promising but not trusted; require the HALF_OPEN ladder.  Move
                # to HALF_OPEN so the next success can close it.
                self._transition_locked(BreakerState.HALF_OPEN)
            # CLOSED stays CLOSED; CREDITS_EXHAUSTED is cleared only by reset().
            return self._state

    def record_failure(
        self,
        *,
        status_code: Optional[int] = None,
        exc: Optional[BaseException] = None,
        reason: Optional[str] = None,
    ) -> BreakerState:
        """Record a failed probe/call and advance the state machine.

        Classifies the failure: a 402 parks the store in ``CREDITS_EXHAUSTED``
        (no flap, no retry-ladder burn); other permanent codes also park there
        (auth/config); transient failures count toward the K-consecutive trip.
        """
        with self._lock:
            self._half_open_probe_inflight = False
            self._last_failure_reason = reason or self._reason_for(status_code, exc)
            c = self._counters
            c.total_failures += 1

            if is_credits_code(status_code) or (
                classify_failure(status_code, exc=exc) is FailureClass.PERMANENT
            ):
                # Permanent: do not flap; park.  Reset consecutive counters so a
                # later recovery starts clean.
                c.consecutive_failures = 0
                c.consecutive_successes = 0
                if self._state is not BreakerState.CREDITS_EXHAUSTED:
                    self._transition_locked(BreakerState.CREDITS_EXHAUSTED)
                return self._state

            c.consecutive_failures += 1
            c.consecutive_successes = 0

            if self._state is BreakerState.HALF_OPEN:
                # A failed gated probe re-opens immediately and bumps cooldown.
                self._open_locked()
            elif self._state is BreakerState.CLOSED:
                if c.consecutive_failures >= self.config.fail_threshold:
                    self._open_locked()
            # Already OPEN / degraded: stay, the failure just refreshes reason.
            return self._state

    def force_state(self, state: BreakerState, *, reason: Optional[str] = None) -> None:
        """Forcibly set a state (used by the watchdog to flip all breakers to
        ``SUPERVISOR_DEGRADED``, and on cold-start restore from ``store_health``)."""
        with self._lock:
            if reason is not None:
                self._last_failure_reason = reason
            if state is BreakerState.OPEN:
                self._open_locked()
            else:
                self._transition_locked(state)

    def reset(self) -> None:
        """Clear the breaker back to CLOSED with fresh counters.  Used to clear
        ``CREDITS_EXHAUSTED`` after an operator tops up credits."""
        with self._lock:
            self._counters = _Counters()
            self._open_until = None
            self._half_open_probe_inflight = False
            self._transition_locked(BreakerState.CLOSED)

    # ------------------------------------------------------------------ #
    # Snapshot / restore
    # ------------------------------------------------------------------ #
    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            self._maybe_half_open_locked()
            c = self._counters
            return BreakerSnapshot(
                store=self.store,
                state=self._state.value,
                consecutive_failures=c.consecutive_failures,
                consecutive_successes=c.consecutive_successes,
                total_failures=c.total_failures,
                total_successes=c.total_successes,
                open_until_monotonic=self._open_until,
                last_change_at=self._last_change_at,
                last_failure_reason=self._last_failure_reason,
            )

    def restore(self, *, state: str, consecutive_failures: int = 0) -> None:
        """Restore breaker state from a persisted ``store_health`` row on cold
        start.  We restore the logical state and failure count but NOT a
        monotonic ``open_until`` (monotonic clocks reset across processes); an
        OPEN store re-derives its cooldown from the current trip count, and the
        next tick re-probes it, which is the safe direction (re-probe early)."""
        with self._lock:
            try:
                st = BreakerState(state)
            except ValueError:
                st = BreakerState.CLOSED
            self._counters.consecutive_failures = max(0, int(consecutive_failures))
            if st is BreakerState.OPEN:
                self._open_locked()
            else:
                self._transition_locked(st)

    # ------------------------------------------------------------------ #
    # Internal helpers (call with the lock held)
    # ------------------------------------------------------------------ #
    def _maybe_half_open_locked(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and self._open_until is not None
            and self._clock() >= self._open_until
        ):
            self._transition_locked(BreakerState.HALF_OPEN)

    def _open_locked(self) -> None:
        c = self._counters
        c.trips += 1
        # Exponential cooldown by trip count, capped, with +/- jitter so multiple
        # stores never stampede recovery at the same instant.
        base = self.config.cooldown_s * (2 ** (c.trips - 1))
        base = min(base, self.config.cooldown_max_s)
        jitter = base * self.config.jitter_frac
        delay = base + self._rng.uniform(-jitter, jitter)
        delay = max(0.0, delay)
        self._open_until = self._clock() + delay
        self._half_open_probe_inflight = False
        self._transition_locked(BreakerState.OPEN)

    def _transition_locked(self, new_state: BreakerState) -> None:
        if new_state is self._state:
            # No-op transitions still refresh nothing; keep last_change_at stable
            # so 'time in state' is meaningful.
            if new_state is not BreakerState.OPEN:
                return
        if new_state is BreakerState.CLOSED:
            self._open_until = None
            self._counters.trips = 0
            self._counters.consecutive_failures = 0
        if new_state is BreakerState.HALF_OPEN:
            self._half_open_probe_inflight = False
            self._counters.consecutive_successes = 0
        self._state = new_state
        self._last_change_at = self._wall()

    def _reason_for(
        self, status_code: Optional[int], exc: Optional[BaseException]
    ) -> str:
        if status_code is not None:
            if is_credits_code(status_code):
                return "insufficient_credits (402)"
            return f"http_{int(status_code)}"
        if exc is not None:
            return f"{type(exc).__name__}: {exc}"
        return "unknown"


class BreakerRegistry:
    """A thread-safe map of store id -> CircuitBreaker.

    The control loop owns one registry; the recall fan-out wrapper asks it
    ``is_open(store)`` to decide whether to skip a store.
    """

    def __init__(
        self,
        config: Optional[BreakerConfig] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        rng: Optional[random.Random] = None,
    ) -> None:
        self._config = config or BreakerConfig()
        self._clock = clock
        self._wall = wall_clock
        self._rng = rng
        self._lock = threading.RLock()
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(self, store: str) -> CircuitBreaker:
        with self._lock:
            b = self._breakers.get(store)
            if b is None:
                b = CircuitBreaker(
                    store,
                    self._config,
                    clock=self._clock,
                    wall_clock=self._wall,
                    rng=self._rng,
                )
                self._breakers[store] = b
            return b

    def is_open(self, store: str) -> bool:
        """True iff the named store's breaker says skip.  Unknown stores are
        treated as CLOSED (fail open: never block a turn for an unknown store)."""
        with self._lock:
            b = self._breakers.get(store)
        if b is None:
            return False
        return b.is_open()

    def all_breakers(self) -> Dict[str, CircuitBreaker]:
        with self._lock:
            return dict(self._breakers)

    def force_all(self, state: BreakerState, *, reason: Optional[str] = None) -> None:
        """Flip every known breaker to *state* (watchdog -> supervisor_degraded)."""
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.force_state(state, reason=reason)
