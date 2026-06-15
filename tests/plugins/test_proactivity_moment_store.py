"""Tests for the moment store (dedup upsert, state machine, notification ledger)."""

from __future__ import annotations

from plugins.proactivity.models import Sensitivity
from plugins.proactivity.moment import Category, MomentState, ProactiveMoment
from plugins.proactivity.moment_store import MomentStore


def _m(key="k1", **over) -> ProactiveMoment:
    base = dict(id="m1", source_id="commitment", category=Category.COMMITMENT,
                title="t", body="b", sensitivity=Sensitivity.TOLD_FACT, dedup_key=key,
                created_at=1.0)
    base.update(over)
    return ProactiveMoment(**base)


def test_upsert_dedups(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    assert s.upsert(_m()) is True       # new
    assert s.upsert(_m()) is False      # same dedup_key -> upsert, not new
    assert len(s.all_moments()) == 1


def test_get_and_by_state(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    s.upsert(_m(key="a"))
    s.upsert(_m(key="b"))
    assert s.get("a") is not None
    assert len(s.pending()) == 2


def test_state_transitions(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    s.upsert(_m(key="a"))
    s.set_state("a", MomentState.SURFACED, surfaced_at=10.0)
    assert s.get("a").state is MomentState.SURFACED
    assert s.get("a").surfaced_at == 10.0
    assert s.awaiting_reply()[0].dedup_key == "a"
    s.set_state("a", MomentState.ACTED, acked_at=20.0)
    assert s.get("a").state is MomentState.ACTED
    assert s.awaiting_reply() == []


def test_digest_queue(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    s.upsert(_m(key="a"))
    s.set_state("a", MomentState.DIGEST)
    assert [m.dedup_key for m in s.digest_queue()] == ["a"]


def test_expire_stale(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    s.upsert(_m(key="a", expires_at=50.0))
    s.upsert(_m(key="b", expires_at=0.0))   # no expiry
    n = s.expire_stale(now=100.0)
    assert n == 1
    assert s.get("a").state is MomentState.EXPIRED
    assert s.get("b").state is MomentState.PENDING


def test_notification_budget_ledger(tmp_path):
    s = MomentStore(tmp_path / "m.db")
    s.record_send(100.0, "push")
    s.record_send(150.0, "push")
    s.record_send(150.0, "digest")  # digest doesn't count toward push budget
    assert s.pushes_since(50.0) == 2
    assert s.pushes_since(120.0) == 1


def test_persists_across_instances(tmp_path):
    db = tmp_path / "m.db"
    MomentStore(db).upsert(_m(key="x"))
    assert MomentStore(db).get("x") is not None
