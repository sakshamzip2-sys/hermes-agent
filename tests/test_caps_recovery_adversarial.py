"""Adversarial probes for caps.py + recovery.py (orchestrator control plane).

Each probe asserts the ROBUST behavior; if the module has a bug the assertion
FAILS and exposes it. Real SQLite, real threads, no mocks of the logic under
test (only the spawn side effect is injected).
"""

from __future__ import annotations

import threading

import pytest

from plugins.oc_orchestrator import caps
from plugins.oc_orchestrator import db as odb
from plugins.oc_orchestrator import recovery


def _reset():
    for attr in ("conn", "path"):
        if hasattr(odb._local, attr):
            try:
                if attr == "conn" and odb._local.conn is not None:
                    odb._local.conn.close()
            except Exception:
                pass
            delattr(odb._local, attr)


@pytest.fixture()
def orch_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_ORCHESTRATOR_DB", str(tmp_path / "oc_orchestrator.db"))
    _reset()
    yield
    _reset()


def _intent_state(conn, intent_id):
    row = conn.execute(
        "SELECT state, child_id FROM spawn_intents WHERE id=?", (intent_id,)
    ).fetchone()
    return row["state"], row["child_id"]


# --------------------------------------------------------------------------- #
# Budget boundary probes
# --------------------------------------------------------------------------- #

def test_budget_zero_refuses_any_positive_est_but_allows_zero(orch_db):
    """A budget of exactly 0 must REFUSE any est_usd > 0 (cannot afford even a
    cent) yet must ALLOW a free (est_usd == 0) spawn."""
    with odb.connect() as conn:
        caps.ensure_tree(conn, "z", budget_usd=0.0)
        # est 0 is free -> allowed
        free = caps.spawn_guarded(conn, "z", depth=1, est_usd=0.0)
        assert free.ok, "zero-cost spawn must be allowed under a 0 budget"
        # any positive cost cannot be afforded -> refused
        paid = caps.spawn_guarded(conn, "z", depth=1, est_usd=0.01)
        assert not paid.ok, "positive-cost spawn must be refused under a 0 budget"
        assert paid.refused_cap == "budget"


def test_budget_exact_spend_allowed_then_overspend_refused(orch_db):
    """Spending the budget to exactly 0 is allowed; the very next positive spend
    is refused. No overspend past the lease budget."""
    with odb.connect() as conn:
        caps.ensure_tree(conn, "b", budget_usd=1.0)
        first = caps.spawn_guarded(conn, "b", depth=1, est_usd=1.0)
        assert first.ok, "spending budget to exactly 0 must be allowed"
        # remaining budget is now 0 -> any further positive spend refused
        second = caps.spawn_guarded(conn, "b", depth=1, est_usd=0.5)
        assert not second.ok and second.refused_cap == "budget"
        # remaining budget must not have gone negative
        row = conn.execute("SELECT budget_usd FROM run_leases WHERE run_tree_id=?", ("b",)).fetchone()
        assert row["budget_usd"] >= 0.0, "budget must never be driven negative"


# --------------------------------------------------------------------------- #
# Depth boundary probes
# --------------------------------------------------------------------------- #

def test_depth_exactly_at_max_passes_one_over_refuses(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "d", caps_overrides={"max_depth": 3, "max_concurrent": 50})
        at_max = caps.spawn_guarded(conn, "d", depth=3)
        assert at_max.ok, "depth exactly at max must be admitted"
        over = caps.spawn_guarded(conn, "d", depth=4)
        assert not over.ok and over.refused_cap == "depth", "depth one over max must be refused"


# --------------------------------------------------------------------------- #
# Fanout boundary probes
# --------------------------------------------------------------------------- #

def test_fanout_exactly_at_max_passes_one_over_refuses(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "f", caps_overrides={"max_fanout": 4, "max_concurrent": 50})
        at_max = caps.spawn_guarded(conn, "f", depth=1, fanout_size=4)
        assert at_max.ok, "fanout exactly at max must be admitted"
        over = caps.spawn_guarded(conn, "f", depth=1, fanout_size=5)
        assert not over.ok and over.refused_cap == "fanout"


# --------------------------------------------------------------------------- #
# Release idempotency / unknown-id probes
# --------------------------------------------------------------------------- #

def test_release_twice_frees_only_once(orch_db):
    """Releasing the same reservation twice must be safe and free exactly one
    concurrency slot, not two. A double-free would corrupt the active count and
    let runaway fan-out slip in."""
    with odb.connect() as conn:
        caps.ensure_tree(conn, "r", caps_overrides={"max_concurrent": 2})
        a = caps.spawn_guarded(conn, "r", depth=1)
        b = caps.spawn_guarded(conn, "r", depth=1)
        assert a.ok and b.ok
        assert recovery.active_reservation_count(conn, "r") == 2

        first = caps.release(conn, a.reservation_id)
        second = caps.release(conn, a.reservation_id)
        assert first is True, "first release of a reserved slot must report freeing it"
        assert second is False, "second release of the same slot must be a no-op (already freed)"
        # exactly one slot freed -> one still reserved
        assert recovery.active_reservation_count(conn, "r") == 1, (
            "double release must free only ONE slot"
        )


def test_release_unknown_reservation_id_is_noop(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "u", caps_overrides={"max_concurrent": 1})
        a = caps.spawn_guarded(conn, "u", depth=1)
        assert a.ok
        before = recovery.active_reservation_count(conn, "u")
        assert caps.release(conn, "does-not-exist") is False
        assert caps.release(conn, None) is False
        assert caps.release(conn, "") is False
        after = recovery.active_reservation_count(conn, "u")
        assert before == after == 1, "releasing an unknown id must not touch the ledger"


# --------------------------------------------------------------------------- #
# Concurrent recovery: exactly-once claim under real thread contention
# --------------------------------------------------------------------------- #

def test_concurrent_attempt_recovery_exactly_one_retried(orch_db):
    """Many threads race attempt_recovery on the SAME (tree, task, failure_seq).
    Exactly one must win 'retried' and spawn exactly once; all others must get
    'already_claimed'. No double-spawn, no double-reserve.

    Each thread uses its own thread-local connection (db.connect is per-thread).
    """
    n_threads = 12
    # seed the lease from the main thread so ensure_tree races don't muddy it
    with odb.connect() as conn:
        caps.ensure_tree(conn, "race", caps_overrides={"max_concurrent": 50})

    spawn_calls = []
    spawn_lock = threading.Lock()

    def spawn_fn(*, attempt_no, intent_id):
        with spawn_lock:
            spawn_calls.append(intent_id)
        return f"child-{intent_id}"

    actions = []
    actions_lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        barrier.wait()
        with odb.connect() as conn:
            r = recovery.attempt_recovery(
                conn,
                run_tree_id="race",
                task_id="task-X",
                failure_seq=42,
                spawn_fn=spawn_fn,
                max_attempts=5,
            )
        with actions_lock:
            actions.append(r.action)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    retried = [a for a in actions if a == "retried"]
    claimed = [a for a in actions if a == "already_claimed"]
    assert len(retried) == 1, f"exactly one recovery must win; got actions={actions}"
    assert len(claimed) == n_threads - 1, f"the rest must be already_claimed; got {actions}"
    assert len(spawn_calls) == 1, "the side effect (spawn) must run exactly once"

    with odb.connect() as conn:
        # exactly one reservation taken, exactly one claim row, one intent
        assert recovery.active_reservation_count(conn, "race") == 1
        claim_rows = conn.execute(
            "SELECT COUNT(*) c FROM recovery_claims WHERE run_tree_id=? AND task_id=? AND failure_seq=?",
            ("race", "task-X", 42),
        ).fetchone()["c"]
        assert claim_rows == 1
        intent_rows = conn.execute(
            "SELECT COUNT(*) c FROM spawn_intents WHERE run_tree_id=? AND task_id=?",
            ("race", "task-X"),
        ).fetchone()["c"]
        assert intent_rows == 1, "no double-spawn-intent under contention"


# --------------------------------------------------------------------------- #
# spawn_fn returning None or raising leaves the intent reconcilable
# --------------------------------------------------------------------------- #

def test_spawn_fn_raises_leaves_intent_reconcilable_no_double_reserve(orch_db):
    """If the spawn side effect raises, the committed intent must stay 'pending'
    (no child) with its single reservation held, and a reconcile tick must
    re-execute it exactly once without a second reservation."""
    raised = {"n": 0}

    def flaky_spawn(*, attempt_no, intent_id):
        if raised["n"] == 0:
            raised["n"] += 1
            raise RuntimeError("boom during spawn")
        return f"child-{intent_id}"

    with odb.connect() as conn:
        r = recovery.attempt_recovery(
            conn, run_tree_id="t", task_id="task-1", failure_seq=7,
            spawn_fn=flaky_spawn, max_attempts=3,
        )
        # decision committed, but spawn crashed before the flip
        assert r.action == "retried"
        assert r.child_id is None
        assert _intent_state(conn, r.intent_id) == ("pending", None)
        assert recovery.active_reservation_count(conn, "t") == 1

        results = recovery.reconcile_intents(conn, flaky_spawn)
        assert len(results) == 1, "exactly one pending intent re-executed"
        state, child = _intent_state(conn, r.intent_id)
        assert state == "launched" and child is not None
        # no second reservation -> still exactly one
        assert recovery.active_reservation_count(conn, "t") == 1


def test_spawn_fn_returning_none_does_not_leak_pending_intent(orch_db):
    """A spawn_fn that returns None is a successful-but-childless spawn. The
    robust contract: the intent must NOT remain re-executable forever (an
    unbounded re-spawn loop). After the first execution it must leave the
    'pending + child_id IS NULL' reconcile set so reconcile_intents does not
    keep re-spawning it on every tick.

    NOTE: _execute_intent flips state to 'launched' and stores child_id=str(None)
    == 'None'. We assert reconcile does not re-pick it (idempotent steady state).
    """
    calls = {"n": 0}

    def none_spawn(*, attempt_no, intent_id):
        calls["n"] += 1
        return None

    with odb.connect() as conn:
        r = recovery.attempt_recovery(
            conn, run_tree_id="t", task_id="task-none", failure_seq=3,
            spawn_fn=none_spawn, max_attempts=3,
        )
        assert r.action == "retried"
        assert calls["n"] == 1
        # reconcile must NOT re-execute it (would be an infinite re-spawn loop)
        results = recovery.reconcile_intents(conn, none_spawn)
        assert results == [], "a None-returning spawn must not be re-spawned every tick"
        assert calls["n"] == 1, "spawn_fn must not be called again by reconcile"


# --------------------------------------------------------------------------- #
# Recovery reserves through the SAME cap ledger (no bypass)
# --------------------------------------------------------------------------- #

def test_recovery_cannot_bypass_concurrency_cap(orch_db):
    """Fill the concurrency ledger with normal spawns, then a recovery must be
    refused (it routes through the same reserve_slot_locked), never spawning."""
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_concurrent": 1})
        a = caps.spawn_guarded(conn, "t", depth=1)
        assert a.ok

        spawned = []

        def spawn_fn(*, attempt_no, intent_id):
            spawned.append(intent_id)
            return "child"

        r = recovery.attempt_recovery(
            conn, run_tree_id="t", task_id="task-1", failure_seq=1,
            spawn_fn=spawn_fn, max_attempts=3,
        )
        assert r.action == "refused" and r.detail == "concurrent"
        assert spawned == [], "recovery must not spawn when the cap is full"
        assert recovery.active_reservation_count(conn, "t") == 1
