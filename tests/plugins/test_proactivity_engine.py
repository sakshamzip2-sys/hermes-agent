"""End-to-end engine tests: sources -> store -> gate -> delivery.

Uses the real stores + config, an injected gateway-delivery fn, and a temp state.db.
The commitment source self-disables when no aux LLM is configured, so this runs without
touching (conflicted) core.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from plugins.proactivity import gateway_delivery
from plugins.proactivity.config import ProactivityConfig
from plugins.proactivity.engine import Engine
from plugins.proactivity.models import EventState, Sensitivity, TrackedEvent
from plugins.proactivity.moment import Category, MomentState, ProactiveMoment
from plugins.proactivity.moment_store import MomentStore
from plugins.proactivity.store import ProactivityStore

NOW = 1_000_000.0
HOUR = 3600.0


@pytest.fixture()
def env(tmp_path):
    es = ProactivityStore(tmp_path / "events.db")
    ms = MomentStore(tmp_path / "moments.db")
    cfg = ProactivityConfig(enabled=True, push_cap_per_day=3, quiet_start_hour=22,
                            quiet_end_hour=8, min_motivation=3, inactivity_days=7)
    eng = Engine(tmp_path / "home", es, ms, cfg)
    return {"es": es, "ms": ms, "cfg": cfg, "eng": eng, "tmp": tmp_path}


@pytest.fixture(autouse=True)
def _reset_delivery():
    gateway_delivery.set_deliver_fn(None)
    yield
    gateway_delivery.set_deliver_fn(None)


def _ended_event(es, eid="e1", title="the summit"):
    es.add_event(TrackedEvent(id=eid, title=title, starts_at=NOW - 2 * HOUR,
                              ends_at=NOW - HOUR, source="user_told",
                              sensitivity=Sensitivity.TOLD_FACT,
                              state=EventState.TRACKED, created_at=NOW - 2 * HOUR))


# -- in-context surfacing ---------------------------------------------------

def test_surface_event_checkin_in_context(env):
    _ended_event(env["es"])
    m = env["eng"].surface_in_context(NOW, local_hour=14)
    assert m is not None and "the summit" in m.body
    # the moment is now SURFACED, awaiting a reply
    assert env["ms"].get(m.dedup_key).state is MomentState.SURFACED


def test_surface_disabled_returns_none(env):
    env["cfg"].enabled = False
    _ended_event(env["es"])
    assert env["eng"].surface_in_context(NOW, local_hour=14) is None


def test_future_dated_moment_not_expired_on_early_poll(env):
    # A moment that isn't relevant yet (future trigger_at) must NOT be retired — it
    # stays PENDING so it can surface later (regression: was wrongly EXPIRED).
    env["ms"].upsert(ProactiveMoment(
        id="future", source_id="commitment", category=Category.COMMITMENT,
        title="ship by Friday", body="You said you'd ship by Friday.",
        sensitivity=Sensitivity.TOLD_FACT, urgency=0.7, dedup_key="future",
        trigger_at=NOW + 5 * HOUR, created_at=NOW,
    ))
    out = env["eng"].surface_in_context(NOW, local_hour=14)
    assert out is None  # not surfaced yet
    assert env["ms"].get("future").state is MomentState.PENDING  # still pending, not expired


def test_surface_one_per_turn(env):
    _ended_event(env["es"], eid="e1", title="event one")
    _ended_event(env["es"], eid="e2", title="event two")
    m = env["eng"].surface_in_context(NOW, local_hour=14)
    assert m is not None
    surfaced = [x for x in env["ms"].all_moments() if x.state is MomentState.SURFACED]
    assert len(surfaced) == 1


# -- capture reply ----------------------------------------------------------

def test_capture_reply_acks_moment_and_event(env):
    _ended_event(env["es"])
    m = env["eng"].surface_in_context(NOW, local_hour=14)
    captured = env["eng"].capture_reply("It went great, met three people!", NOW + 10)
    assert captured == (m.title, "It went great, met three people!")
    assert env["ms"].get(m.dedup_key).state is MomentState.ACTED
    # underlying event also acked
    assert env["es"].get("e1").state is EventState.ACKED


def test_capture_reply_ignores_stale(env):
    _ended_event(env["es"])
    env["eng"].surface_in_context(NOW, local_hour=14)
    assert env["eng"].capture_reply("unrelated", NOW + 3 * HOUR) is None  # beyond window


# -- background push + digest ----------------------------------------------

def _seed_pending(ms, key="c1", urgency=0.9, sensitivity=Sensitivity.USER_LOOP,
                  category=Category.COMMITMENT):
    ms.upsert(ProactiveMoment(id=key, source_id="commitment", category=category,
                              title="email Sam", body="You said you'd email Sam.",
                              urgency=urgency, sensitivity=sensitivity, dedup_key=key,
                              created_at=NOW))


def test_background_pushes_urgent_via_gateway(env):
    sent = []
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: sent.append(content) or None)
    _seed_pending(env["ms"])
    summary = asyncio.run(env["eng"].run_background(NOW, None, local_hour=14))
    assert summary["pushed"] == 1
    assert sent and "email Sam" in sent[0]
    assert env["ms"].pushes_since(NOW - HOUR) == 1


def test_background_digests_when_over_budget(env):
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: None)
    # exhaust the budget
    for i in range(env["cfg"].push_cap_per_day):
        env["ms"].record_send(NOW, "push")
    _seed_pending(env["ms"])
    summary = asyncio.run(env["eng"].run_background(NOW, None, local_hour=14))
    assert summary["pushed"] == 0
    assert summary["digested"] == 1


def test_background_quiet_hours_digests(env):
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: None)
    _seed_pending(env["ms"])
    summary = asyncio.run(env["eng"].run_background(NOW, None, local_hour=23))  # quiet
    assert summary["pushed"] == 0
    assert summary["digested"] == 1


def test_deliver_digest_via_gateway(env):
    sent = []
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: sent.append(content) or None)
    _seed_pending(env["ms"], key="d1")
    env["ms"].set_state("d1", MomentState.DIGEST)
    ok = env["eng"].deliver_digest(NOW)
    assert ok is True
    assert sent and "email Sam" in sent[0]
    assert env["ms"].get("d1").state is MomentState.DELIVERED


def test_background_disabled_noop(env):
    env["cfg"].enabled = False
    _seed_pending(env["ms"])
    summary = asyncio.run(env["eng"].run_background(NOW, None, local_hour=14))
    assert summary == {"polled": 0, "pushed": 0, "digested": 0}


# -- inactivity source (re-engagement never pushes) ------------------------

def _make_state_db(path: Path, last_user_ts: float) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL)")
    conn.execute("INSERT INTO messages(role, content, timestamp) VALUES ('user', 'hello', ?)", (last_user_ts,))
    conn.commit()
    conn.close()


def test_inactivity_emits_reengagement_but_never_pushes(env):
    db = env["tmp"] / "state.db"
    _make_state_db(db, last_user_ts=NOW - 10 * 86400.0)  # 10 days silent
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: None)
    summary = asyncio.run(env["eng"].run_background(NOW, db, local_hour=14))
    # a re-engagement moment was emitted, but it is push-forbidden -> digested, not pushed
    assert summary["pushed"] == 0
    cats = {m.category for m in env["ms"].all_moments()}
    assert Category.RE_ENGAGEMENT in cats
