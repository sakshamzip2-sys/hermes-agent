"""Tests for the Runtime Memory Supervisor (RMS) plugin (req #12a / GAP-3).

Stdlib + pytest only, no network, no LLM, no live servers.  Every test runs
against an isolated SQLite state DB (``HERMES_MEM_SUPERVISOR_DB`` -> tmp) and
injects fake probes / drainers / clocks, so the full machine (circuit breaker,
durable fail-closed write queue, job supervision, watchdog, resumability) is
exercised deterministically.

Proves, per the brief:
  (a) the circuit breaker transitions closed->open->half_open->closed correctly
      and OPEN skips the call FAST (no probe / no network);
  (b) a simulated store outage is DETECTED and surfaced in get_health()
      (not silent) and recovery flips it back;
  (c) a write to a down store is QUEUED (fail-closed) and drained when the store
      recovers, never dropped, idempotent on re-drain;
  (d) a write that permanently fails goes to dead_letter (still in the DB);
  (e) the loop tick is try/except-wrapped so a probe exception never kills the
      supervisor (no cascade);
  (f) the migration/state-db is additive and the supervisor is resumable
      (reopen the DB -> queued writes survive).
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Tuple

import pytest

from plugins.memory_supervisor import wal
from plugins.memory_supervisor.breaker import (
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
    FailureClass,
    classify_failure,
)
from plugins.memory_supervisor.control_loop import MemorySupervisor, SupervisorConfig
from plugins.memory_supervisor.probes import ProbeResult


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def state_db(tmp_path, monkeypatch):
    """Isolate the supervisor state DB to a temp file and reset the cached
    thread-local connection before and after each test."""
    monkeypatch.setenv("HERMES_MEM_SUPERVISOR_DB", str(tmp_path / "mem_supervisor.db"))
    wal.close_local()
    yield tmp_path
    wal.close_local()


class FakeClock:
    """A manually advanced clock used for BOTH the monotonic breaker clock and
    the wall-clock backoff gate, so every time-dependent transition is
    deterministic without sleeping."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _supervisor(
    clock: FakeClock,
    *,
    stores: List[str],
    probes: Dict[str, object],
    drainer=None,
    fail_threshold: int = 2,
    recover_successes: int = 1,
    cooldown_s: float = 10.0,
) -> MemorySupervisor:
    cfg = SupervisorConfig(
        tick_interval_s=1.0,
        breaker=BreakerConfig(
            fail_threshold=fail_threshold,
            recover_successes=recover_successes,
            cooldown_s=cooldown_s,
            jitter_frac=0.0,
        ),
        backoff_jitter_frac=0.0,
        backoff_base_s=2.0,
        write_max_attempts=3,
    )
    return MemorySupervisor(
        cfg,
        stores=stores,
        probes=probes,  # type: ignore[arg-type]
        drainer=drainer,  # type: ignore[arg-type]
        clock=clock,
        wall_clock=clock,
        rng=random.Random(0),
    )


# --------------------------------------------------------------------------- #
# (a) Circuit breaker state machine + OPEN skips fast
# --------------------------------------------------------------------------- #

def test_breaker_closed_to_open_to_half_open_to_closed():
    clock = FakeClock()
    b = CircuitBreaker(
        "s",
        BreakerConfig(fail_threshold=3, recover_successes=2, cooldown_s=30.0, jitter_frac=0.0),
        clock=clock,
        wall_clock=clock,
    )
    assert b.state is BreakerState.CLOSED
    assert b.allow_request() is True

    # K=3 consecutive failures to OPEN; not before.
    b.record_failure(exc=TimeoutError())
    b.record_failure(exc=TimeoutError())
    assert b.state is BreakerState.CLOSED  # debounced: needs 3
    b.record_failure(exc=TimeoutError())
    assert b.state is BreakerState.OPEN

    # OPEN -> allow_request is False (skip fast), before the cooldown elapses.
    assert b.allow_request() is False
    assert b.is_open() is True

    # After the cooldown the breaker lazily becomes HALF_OPEN.
    clock.advance(31.0)
    assert b.state is BreakerState.HALF_OPEN
    # HALF_OPEN admits exactly one gated probe.
    assert b.allow_request() is True
    assert b.allow_request() is False  # second concurrent caller is blocked

    # M=2 consecutive successes to CLOSE (hysteresis): one is not enough.
    b.record_success()
    assert b.state is BreakerState.HALF_OPEN
    # Re-enter the probe slot for the next success.
    assert b.allow_request() is True
    b.record_success()
    assert b.state is BreakerState.CLOSED


def test_breaker_half_open_failure_reopens():
    clock = FakeClock()
    b = CircuitBreaker(
        "s", BreakerConfig(fail_threshold=1, recover_successes=1, cooldown_s=10.0, jitter_frac=0.0),
        clock=clock, wall_clock=clock,
    )
    b.record_failure(exc=ConnectionError())
    assert b.state is BreakerState.OPEN
    clock.advance(11.0)
    assert b.state is BreakerState.HALF_OPEN
    b.record_failure(exc=ConnectionError())  # failed gated probe
    assert b.state is BreakerState.OPEN


def test_breaker_open_skips_call_without_invoking_probe():
    """OPEN must short-circuit in O(1): the supervisor must NOT call the probe
    for an OPEN store (that is the whole point - no per-turn timeout)."""
    clock = FakeClock()
    calls = {"n": 0}

    def probe() -> ProbeResult:
        calls["n"] += 1
        return ProbeResult(ok=False, status_code=503)

    sup = _supervisor(clock, stores=["s1"], probes={"s1": probe}, fail_threshold=1, cooldown_s=100.0)
    sup._tick()  # one failing probe -> OPEN
    assert calls["n"] == 1
    assert sup.recall_allowed("s1") is False
    before = calls["n"]
    # Subsequent ticks while OPEN (cooldown not elapsed) must skip the probe.
    sup._tick()
    sup._tick()
    assert calls["n"] == before  # no extra probe calls while OPEN -> skip fast


def test_failure_classification():
    assert classify_failure(503) is FailureClass.TRANSIENT
    assert classify_failure(429) is FailureClass.TRANSIENT
    assert classify_failure(None, exc=TimeoutError()) is FailureClass.TRANSIENT
    assert classify_failure(402) is FailureClass.PERMANENT
    assert classify_failure(401) is FailureClass.PERMANENT
    assert classify_failure(403) is FailureClass.PERMANENT


def test_breaker_402_parks_credits_exhausted_without_flapping():
    clock = FakeClock()
    b = CircuitBreaker(
        "s", BreakerConfig(fail_threshold=3, cooldown_s=30.0, jitter_frac=0.0),
        clock=clock, wall_clock=clock,
    )
    b.record_failure(status_code=402)
    assert b.state is BreakerState.CREDITS_EXHAUSTED
    assert b.is_open() is True  # skip embedding-dependent calls fast
    # A 402 does not 'count up' toward a transient trip; cleared only by reset.
    b.reset()
    assert b.state is BreakerState.CLOSED


# --------------------------------------------------------------------------- #
# (b) A simulated store outage is DETECTED + surfaced (not silent), recovery flips back
# --------------------------------------------------------------------------- #

def test_outage_is_detected_and_surfaced_in_health_then_recovers(state_db):
    clock = FakeClock()
    up = {"v": True}

    def probe() -> ProbeResult:
        return ProbeResult(ok=up["v"], status_code=None if up["v"] else 503)

    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": probe}, fail_threshold=2, cooldown_s=10.0)

    sup._tick()
    health = sup.get_health()
    assert health["stores"]["honcho"]["state"] == "closed"
    assert health["stores"]["honcho"]["down"] is False

    # Outage: two failing probes -> OPEN, and it is VISIBLE in the health table
    # (this is the fix for the silent-degradation finding).
    up["v"] = False
    sup._tick()
    sup._tick()
    health = sup.get_health()
    assert health["stores"]["honcho"]["state"] == "open"
    assert health["stores"]["honcho"]["down"] is True
    assert health["stores"]["honcho"]["consecutive_failures"] >= 1
    assert sup.recall_allowed("honcho") is False  # recall fails open: skip it

    # Recovery: after the cooldown a gated probe succeeds and closes the breaker.
    up["v"] = True
    clock.advance(11.0)
    sup._tick()
    health = sup.get_health()
    assert health["stores"]["honcho"]["state"] == "closed"
    assert health["stores"]["honcho"]["down"] is False
    assert sup.recall_allowed("honcho") is True


def test_health_row_persists_outage_for_aggregator(state_db):
    """The outage must be readable straight from the durable store_health table
    (what the aggregator/CLI reads), not just from in-memory breaker state."""
    clock = FakeClock()

    def down_probe() -> ProbeResult:
        return ProbeResult(ok=False, status_code=503)

    sup = _supervisor(clock, stores=["gbrain"], probes={"gbrain": down_probe}, fail_threshold=1)
    sup._tick()
    rows = wal.get_store_health("gbrain")
    assert len(rows) == 1
    assert rows[0]["state"] == "open"
    assert rows[0]["last_probe_at"] is not None


# --------------------------------------------------------------------------- #
# (c) Write to a down store is QUEUED (fail-closed) + drained on recovery + idempotent
# --------------------------------------------------------------------------- #

def test_write_to_down_store_is_queued_then_drained_idempotently(state_db):
    clock = FakeClock()
    up = {"v": False}
    applied: List[str] = []

    def probe() -> ProbeResult:
        return ProbeResult(ok=up["v"], status_code=None if up["v"] else 503)

    def drainer(row) -> Tuple[bool, Optional[int], Optional[str]]:
        if not up["v"]:
            return (False, 503, "unavailable")
        # Idempotent apply: dedup_key guards against double-apply on re-drain.
        applied.append(row["dedup_key"])
        return (True, None, None)

    sup = _supervisor(
        clock, stores=["honcho"], probes={"honcho": probe}, drainer=drainer,
        fail_threshold=2, cooldown_s=10.0,
    )

    # Store is down: the write must be durably QUEUED (fail-closed), not dropped.
    key = sup.enqueue_write("honcho", {"fact": "user likes teal"})
    assert wal.queue_depth("honcho", status="pending") == 1

    # Idempotent enqueue: re-enqueuing the same logical write (volatile ts
    # excluded) is a no-op that returns the same key.
    key2 = sup.enqueue_write("honcho", {"fact": "user likes teal", "ts": 999})
    assert key == key2
    assert wal.queue_depth("honcho", status="pending") == 1

    # Tick while down -> breaker trips; the write is NOT dropped, stays queued.
    sup._tick()
    sup._tick()
    assert sup.recall_allowed("honcho") is False
    assert wal.queue_depth("honcho", status="pending") == 1  # never dropped

    # Store recovers -> the queued write drains exactly once.
    up["v"] = True
    clock.advance(100.0)
    sup._tick()
    assert wal.queue_depth("honcho", status="pending") == 0
    assert wal.queue_depth("honcho", status="done") == 1
    assert applied.count(key) == 1  # applied exactly once

    # Re-drain idempotency: a re-enqueue of the same key after it is done does
    # not create a second row (INSERT OR IGNORE), so no double-apply.
    sup.enqueue_write("honcho", {"fact": "user likes teal"})
    sup._tick()
    assert applied.count(key) == 1


def test_write_is_never_dropped_even_with_no_drainer(state_db):
    """With no provider wired (drainer=None) the write path is still fail-closed:
    the write is durably queued and survives, just not drained yet."""
    clock = FakeClock()
    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=False, status_code=503)}, drainer=None)
    sup.enqueue_write("honcho", {"fact": "x"})
    sup._tick()
    assert wal.queue_depth("honcho", status="pending") == 1  # queued, not lost


# --------------------------------------------------------------------------- #
# (d) A write that permanently fails goes to dead_letter (still in the DB)
# --------------------------------------------------------------------------- #

def test_permanent_failure_dead_letters(state_db):
    clock = FakeClock()

    def probe() -> ProbeResult:
        return ProbeResult(ok=True)  # store is 'up' but the write itself is rejected

    def drainer(row) -> Tuple[bool, Optional[int], Optional[str]]:
        return (False, 400, "bad request: malformed payload")  # permanent

    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": probe}, drainer=drainer)
    sup.enqueue_write("honcho", {"fact": "bad"})
    sup._tick()
    # A permanent (4xx) failure dead-letters immediately, but the row is STILL in
    # the DB (never lost), in the dead_letter state.
    assert wal.queue_depth("honcho", status="pending") == 0
    assert wal.dead_letter_count("honcho") == 1
    dead = wal.list_writes(status="dead_letter")
    assert len(dead) == 1
    assert dead[0]["last_error"]


def test_transient_failure_dead_letters_after_budget(state_db):
    """A persistently-failing transient write exhausts its attempt budget and
    dead-letters, still in the DB."""
    clock = FakeClock()
    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=True)},
                      drainer=lambda row: (False, 500, "boom"))
    sup.enqueue_write("honcho", {"fact": "x"})
    # write_max_attempts=3; drive enough ticks (advancing the clock past each
    # backoff gate) to exhaust the budget.
    for _ in range(6):
        clock.advance(200.0)
        sup._tick()
    assert wal.dead_letter_count("honcho") == 1
    assert wal.queue_depth("honcho", status="pending") == 0


# --------------------------------------------------------------------------- #
# (e) The loop tick is try/except-wrapped: a probe exception never kills it
# --------------------------------------------------------------------------- #

def test_probe_exception_does_not_kill_the_loop(state_db):
    clock = FakeClock()
    state = {"raise": True}

    def exploding_probe() -> ProbeResult:
        if state["raise"]:
            raise RuntimeError("probe blew up")
        return ProbeResult(ok=True)

    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": exploding_probe}, fail_threshold=1)
    # A probe that raises must be caught (counted as a failure), never propagate.
    sup._tick()
    health = sup.get_health()
    assert health["stores"]["honcho"]["state"] in ("open", "closed", "half_open")
    # The supervisor is still usable: enqueue + a clean tick still works.
    state["raise"] = False
    clock.advance(100.0)
    sup._tick()
    # No exception escaped; get_health still returns a coherent view.
    assert "stores" in sup.get_health()


def test_loop_continues_after_drainer_exception(state_db):
    clock = FakeClock()

    def boom_drainer(row) -> Tuple[bool, Optional[int], Optional[str]]:
        raise ValueError("drainer crashed")

    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=True)},
                      drainer=boom_drainer)
    sup.enqueue_write("honcho", {"fact": "x"})
    sup._tick()  # drainer raises -> treated as a transient failure, loop survives
    # The write is still in the DB (re-queued for retry), never lost.
    total = wal.queue_depth("honcho", status="pending") + wal.dead_letter_count("honcho")
    assert total == 1


# --------------------------------------------------------------------------- #
# (f) Additive migration + resumability: reopen the DB -> queued writes survive
# --------------------------------------------------------------------------- #

def test_state_db_is_additive_and_resumable(state_db):
    clock = FakeClock()
    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=False, status_code=503)}, drainer=None)
    key = sup.enqueue_write("honcho", {"fact": "durable across restart"})
    sup._tick()
    assert wal.queue_depth("honcho", status="pending") == 1

    # Simulate a gateway restart: stop the old supervisor (the OS reclaims its
    # flock + lease on a real process exit), drop the connection, and build a
    # brand new supervisor against the SAME DB path.
    sup.stop(timeout=0.5)
    wal.close_local()
    clock2 = FakeClock(t=clock.t)
    up = {"v": True}
    applied: List[str] = []

    def probe() -> ProbeResult:
        return ProbeResult(ok=up["v"])

    def drainer(row) -> Tuple[bool, Optional[int], Optional[str]]:
        applied.append(row["dedup_key"])
        return (True, None, None)

    sup2 = _supervisor(clock2, stores=["honcho"], probes={"honcho": probe}, drainer=drainer)
    # The queued write SURVIVED the restart (resumable).
    assert wal.get_write(key) is not None
    assert wal.queue_depth("honcho", status="pending") == 1
    # And drains on the new process once the store is up.
    clock2.advance(100.0)
    sup2._tick()
    assert wal.queue_depth("honcho", status="done") == 1
    assert applied == [key]


def test_reopen_db_does_not_destroy_existing_rows(state_db):
    """Reopening the DB re-runs CREATE TABLE IF NOT EXISTS (additive); existing
    rows must be intact (no destructive migration)."""
    wal.enqueue_write("honcho", {"a": 1})
    wal.enqueue_write("gbrain", {"b": 2})
    assert wal.queue_depth("honcho") == 1
    assert wal.queue_depth("gbrain") == 1
    # Force a fresh connection (re-applies schema) and confirm rows survive.
    wal.close_local()
    assert wal.queue_depth("honcho") == 1
    assert wal.queue_depth("gbrain") == 1


# --------------------------------------------------------------------------- #
# Job supervision + watchdog + queue cap + start hook
# --------------------------------------------------------------------------- #

def test_stuck_job_is_reconciled_then_dead_letters(state_db):
    """A job whose heartbeat is stale (owner provably dead by wall-clock cap, not
    bare pid) is re-enqueued under the retry budget and dead-letters past it."""
    wal.enqueue_job("compaction", period_key="2026-06-21", max_attempts=2)
    job = wal.list_jobs(status="pending")[0]
    # Claim it, then never heartbeat -> it is stuck.
    assert wal.claim_job(int(job["id"]), lease_token="tok", lease_s=10.0)
    now = wal._now()
    # First reconcile after the stuck window -> re-enqueued (attempt 1).
    n = wal.reconcile_jobs(stuck_after_s=5.0, now=now + 100.0)
    assert n == 1
    assert wal.list_jobs(status="pending")
    # Claim + stall again -> exceeds max_attempts=2 -> dead_letter.
    job = wal.list_jobs(status="pending")[0]
    wal.claim_job(int(job["id"]), lease_token="tok2", lease_s=10.0)
    wal.reconcile_jobs(stuck_after_s=5.0, now=now + 200.0)
    assert wal.list_jobs(status="dead_letter")


def test_job_enqueue_is_idempotent_by_period_key(state_db):
    assert wal.enqueue_job("eval", period_key="2026-06-21") is True
    assert wal.enqueue_job("eval", period_key="2026-06-21") is False  # idempotent
    assert wal.enqueue_job("eval", period_key="2026-06-22") is True  # different window
    assert len(wal.list_jobs()) == 2


def test_watchdog_detects_stale_heartbeat(state_db):
    clock = FakeClock()
    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=True)})
    sup._tick()
    # Fresh heartbeat -> watchdog reports healthy.
    assert sup.watchdog_check() is True
    # Stale heartbeat: advance wall clock far beyond watchdog window without a
    # tick.  Because watchdog uses real wall-clock via wal.heartbeat_stale on the
    # last recorded tick, we record an old tick directly.
    old = wal._now() - (sup.config.watchdog_factor * sup.config.tick_interval_s) - 100.0
    wal.record_tick(boot_id="b", pid=1, now=old)
    assert wal.heartbeat_stale(max_age_s=sup.config.watchdog_factor * sup.config.tick_interval_s) is True


def test_queue_cap_evicts_oldest_to_dead_letter_never_silently(state_db):
    # max_depth small so overflow is easy to trigger.
    n = wal.queue_depth("honcho")
    for i in range(5):
        wal.enqueue_write("honcho", {"i": i})
    assert wal.queue_depth("honcho", status="pending") == 5
    evicted = wal.evict_oldest_over_cap("honcho", max_depth=3)
    assert evicted == 2
    assert wal.queue_depth("honcho", status="pending") == 3
    # Evicted rows are NOT lost: they are dead-lettered.
    assert wal.dead_letter_count("honcho") == 2


def test_start_hook_disabled_by_default_changes_nothing(state_db, monkeypatch):
    """The start hook is opt-in: without config it returns False and does not
    start anything (absence == today's behavior)."""
    from plugins.memory_supervisor import registry

    # No config -> disabled.
    monkeypatch.setattr(registry, "_resolve_config", lambda: {})
    registry.stop_memory_supervisor()
    assert registry.start_memory_supervisor() is False
    assert registry.is_running() is False
    assert registry.get_memory_supervisor() is None


def test_start_hook_force_starts_and_is_idempotent(state_db, monkeypatch):
    from plugins.memory_supervisor import registry

    monkeypatch.setattr(registry, "_resolve_config", lambda: {"enabled": True, "tick_interval_s": 60})
    registry.stop_memory_supervisor()
    try:
        assert registry.start_memory_supervisor() is True
        assert registry.is_running() is True
        # Idempotent: a second start while running returns False.
        assert registry.start_memory_supervisor() is False
        assert registry.get_memory_supervisor() is not None
    finally:
        registry.stop_memory_supervisor()
    assert registry.is_running() is False


def test_unknown_store_recall_fails_open(state_db):
    """recall_allowed for a store with no breaker yet must return True (never
    block a turn for an unknown store)."""
    clock = FakeClock()
    sup = _supervisor(clock, stores=["honcho"], probes={"honcho": lambda: ProbeResult(ok=True)})
    assert sup.recall_allowed("a-store-we-never-probed") is True
