"""Tests for orchestrator self-healing: leader lease + recover_all sweep.

The driver must be a singleton (leader-leased fencing token) and must recover its
own in-flight recovery intents on restart. Real SQLite, no mocks.
"""

from __future__ import annotations

import pytest

from plugins.oc_orchestrator import db as odb
from plugins.oc_orchestrator import lease, recovery


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


# --------------------------------------------------------------------------- #
# leader lease
# --------------------------------------------------------------------------- #

def test_first_acquire_gets_token_one(orch_db):
    with odb.connect() as conn:
        lz = lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1000.0)
        assert lz.acquired is True
        assert lz.fencing_token == 1
        assert lz.holder == "gw-A"


def test_same_holder_renews_same_token(orch_db):
    with odb.connect() as conn:
        lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1000.0)
        lz = lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1010.0)
        assert lz.acquired is True
        assert lz.fencing_token == 1
        assert lz.expires_at == 1040.0


def test_second_holder_blocked_while_live(orch_db):
    with odb.connect() as conn:
        lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1000.0)
        lz = lease.acquire_or_renew(conn, "gw-B", ttl=30.0, now=1005.0)
        assert lz.acquired is False  # A still holds it live; no split-brain
        assert lz.holder == "gw-A"


def test_stale_lease_taken_over_and_token_bumped(orch_db):
    with odb.connect() as conn:
        lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1000.0)
        # Past A's expiry: B takes over and the fencing token bumps so a
        # resurrected A (with token 1) can be detected.
        lz = lease.acquire_or_renew(conn, "gw-B", ttl=30.0, now=1031.0)
        assert lz.acquired is True
        assert lz.holder == "gw-B"
        assert lz.fencing_token == 2


def test_release_lets_another_acquire(orch_db):
    with odb.connect() as conn:
        lease.acquire_or_renew(conn, "gw-A", ttl=30.0, now=1000.0)
        lease.release(conn, "gw-A")
        lz = lease.acquire_or_renew(conn, "gw-B", ttl=30.0, now=1005.0)
        assert lz.acquired is True
        assert lz.holder == "gw-B"


# --------------------------------------------------------------------------- #
# recover_all restart sweep
# --------------------------------------------------------------------------- #

class FakeSpawner:
    def __init__(self, fail_times=0):
        self.calls = []
        self.fail_times = fail_times

    def __call__(self, *, attempt_no, intent_id):
        self.calls.append((attempt_no, intent_id))
        if len(self.calls) <= self.fail_times:
            raise RuntimeError("crash before flip")
        return f"child-{attempt_no}"


def test_recover_all_re_executes_pending_intent(orch_db):
    with odb.connect() as conn:
        # A prior recovery created an intent but crashed before the spawn flipped.
        sp = FakeSpawner(fail_times=1)
        r = recovery.attempt_recovery(conn, run_tree_id="t", task_id="task-1",
                                      failure_seq=1, spawn_fn=sp, max_attempts=3)
        assert r.child_id is None  # crashed: intent left pending

        # On restart, recover_all re-executes the pending intent exactly once.
        summary = recovery.recover_all(conn, sp)
        assert summary["pending_re_executed"] == 1
        assert summary["launched"] == 1
        assert recovery.active_reservation_count(conn, "t") == 1  # no double reserve

        # Idempotent: a second sweep finds nothing pending.
        summary2 = recovery.recover_all(conn, sp)
        assert summary2["pending_re_executed"] == 0
