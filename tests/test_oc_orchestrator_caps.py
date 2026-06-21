"""Tests for spawn_guarded, the single atomic cap ledger (Feature C).

spawn_guarded is the ONLY path to any spawn. It reserves a slot in one
slot_reservations row via a single BEGIN IMMEDIATE compare-and-swap, so the
counted resource and the lock are the same row in the same DB. This makes
runaway fan-out impossible by construction: the 8-thread contention test proves
that when only N slots exist, exactly N concurrent admits succeed and the rest
are refused, with no lost-update overshoot.

Stdlib + pytest only, real SQLite, real threads, no mocks.
"""

from __future__ import annotations

import threading

import pytest

from plugins.oc_orchestrator import caps
from plugins.oc_orchestrator import db as odb


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


def test_reserve_under_concurrency_cap_then_refuse(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "tree1", caps_overrides={"max_concurrent": 3})
        d1 = caps.spawn_guarded(conn, "tree1", depth=1)
        d2 = caps.spawn_guarded(conn, "tree1", depth=1)
        d3 = caps.spawn_guarded(conn, "tree1", depth=1)
        assert d1.ok and d2.ok and d3.ok
        d4 = caps.spawn_guarded(conn, "tree1", depth=1)
        assert not d4.ok
        assert d4.refused_cap == "concurrent"


def test_release_frees_a_concurrency_slot(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_concurrent": 1})
        d1 = caps.spawn_guarded(conn, "t", depth=1)
        assert d1.ok
        assert not caps.spawn_guarded(conn, "t", depth=1).ok
        caps.release(conn, d1.reservation_id)
        assert caps.spawn_guarded(conn, "t", depth=1).ok


def test_depth_cap_refuses(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_depth": 2})
        assert caps.spawn_guarded(conn, "t", depth=2).ok
        d = caps.spawn_guarded(conn, "t", depth=3)
        assert not d.ok and d.refused_cap == "depth"


def test_fanout_cap_refuses(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_fanout": 4})
        d = caps.spawn_guarded(conn, "t", depth=1, fanout_size=5)
        assert not d.ok and d.refused_cap == "fanout"


def test_budget_pre_spend_refuses(orch_db):
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", budget_usd=1.0)
        assert caps.spawn_guarded(conn, "t", depth=1, est_usd=0.6).ok
        d = caps.spawn_guarded(conn, "t", depth=1, est_usd=0.6)
        assert not d.ok and d.refused_cap == "budget"


def test_max_spawns_backstop_is_monotonic(orch_db):
    # Releasing frees concurrency but must NOT decrement the spawn backstop.
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_spawns": 2, "max_concurrent": 10})
        a = caps.spawn_guarded(conn, "t", depth=1)
        caps.release(conn, a.reservation_id)
        b = caps.spawn_guarded(conn, "t", depth=1)
        caps.release(conn, b.reservation_id)
        d = caps.spawn_guarded(conn, "t", depth=1)
        assert not d.ok and d.refused_cap == "max_spawns"


def test_hard_ceiling_clamps_configured_cap(orch_db):
    # A config asking for more than the hard ceiling is clamped to the ceiling.
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t", caps_overrides={"max_concurrent": 10_000})
        granted = 0
        for _ in range(caps.HARD_CEILINGS["max_concurrent"] + 5):
            if caps.spawn_guarded(conn, "t", depth=1).ok:
                granted += 1
        assert granted == caps.HARD_CEILINGS["max_concurrent"]


def test_eight_thread_contention_exactly_n_succeed(orch_db):
    """Runaway-fan-out-impossible proof: 8 threads race for 5 slots; exactly 5
    win. Each thread uses its own connection (db.connect is thread-local)."""
    caps_n = 5
    with odb.connect() as conn:
        caps.ensure_tree(conn, "race", caps_overrides={"max_concurrent": caps_n})

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        with odb.connect() as conn:
            d = caps.spawn_guarded(conn, "race", depth=1)
        with lock:
            results.append(d.ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for ok in results if ok) == caps_n
    assert sum(1 for ok in results if not ok) == 8 - caps_n
