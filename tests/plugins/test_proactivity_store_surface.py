"""Store CRUD + end-to-end surface/capture/feedback flow."""

from __future__ import annotations

import pytest

from plugins.proactivity import surface
from plugins.proactivity.cadence import CadenceTuning, effective_push_cap, load_cadence
from plugins.proactivity.config import ProactivityConfig
from plugins.proactivity.models import EventState, Sensitivity, TrackedEvent
from plugins.proactivity.store import ProactivityStore

NOW = 1_000_000.0
HOUR = 3600.0


@pytest.fixture()
def store(tmp_path):
    return ProactivityStore(tmp_path / "p.db")


def _ended_event(eid="e1", title="the meetup", ago_hours=1.0,
                 sensitivity=Sensitivity.TOLD_FACT, source="user_told"):
    return TrackedEvent(
        id=eid, title=title, starts_at=NOW - ago_hours * HOUR - HOUR,
        ends_at=NOW - ago_hours * HOUR, source=source,
        sensitivity=sensitivity, state=EventState.TRACKED, created_at=NOW - 10 * HOUR,
    )


# -- store ------------------------------------------------------------------

def test_store_add_get(store):
    ev = _ended_event()
    store.add_event(ev)
    got = store.get("e1")
    assert got is not None and got.title == "the meetup"


def test_store_state_transitions(store):
    store.add_event(_ended_event())
    store.mark_surfaced("e1", NOW)
    assert store.get("e1").state is EventState.SURFACED
    store.mark_acked("e1", NOW + 1)
    assert store.get("e1").state is EventState.ACKED


def test_promote_ended_to_pending(store):
    store.add_event(_ended_event())
    promoted = store.promote_ended_to_pending(NOW)
    assert len(promoted) == 1
    assert store.get("e1").state is EventState.PENDING


def test_promote_skips_future_events(store):
    fut = _ended_event(ago_hours=-2.0)  # ends 2h in the future
    store.add_event(fut)
    assert store.promote_ended_to_pending(NOW) == []
    assert store.get("e1").state is EventState.TRACKED


def test_pushes_since(store):
    store.add_event(_ended_event())
    store.mark_pushed("e1", NOW)
    assert store.pushes_since(NOW - HOUR) == 1
    assert store.pushes_since(NOW + HOUR) == 0


# -- surface (end to end) ---------------------------------------------------

def _cfg(**over):
    base = dict(enabled=True, push_cap_per_day=1, quiet_start_hour=22,
                quiet_end_hour=8, cadence_evolution=True, event_ttl_days=14)
    base.update(over)
    return ProactivityConfig(**base)


def test_disabled_config_no_injection(store):
    store.add_event(_ended_event())
    out = surface.build_injection(store, _cfg(enabled=False), CadenceTuning(),
                                  now=NOW, local_hour=14)
    assert out is None


def test_in_context_checkin_surfaces(store):
    store.add_event(_ended_event())
    out = surface.build_injection(store, _cfg(), CadenceTuning(),
                                  now=NOW, local_hour=14,
                                  opened_conversation_since_end=True)
    assert out is not None and "the meetup" in out
    assert store.get("e1").state is EventState.SURFACED


def test_sensitive_event_never_surfaces(store):
    store.add_event(_ended_event(sensitivity=Sensitivity.SENSITIVE))
    out = surface.build_injection(store, _cfg(), CadenceTuning(),
                                  now=NOW, local_hour=14)
    assert out is None


def test_muted_keyword_suppresses(store):
    store.add_event(_ended_event(source="calendar"))
    tuning = CadenceTuning(muted_keywords=("calendar",))
    out = surface.build_injection(store, _cfg(), tuning, now=NOW, local_hour=14)
    assert out is None


def test_ttl_expiry(store):
    # ended 30 days ago, ttl 14 days -> expired, not surfaced
    store.add_event(_ended_event(ago_hours=24 * 30))
    out = surface.build_injection(store, _cfg(), CadenceTuning(), now=NOW, local_hour=14)
    assert out is None
    assert store.get("e1").state is EventState.EXPIRED


def test_only_one_checkin_per_turn(store):
    store.add_event(_ended_event(eid="e1", title="meetup one"))
    store.add_event(_ended_event(eid="e2", title="meetup two"))
    out = surface.build_injection(store, _cfg(), CadenceTuning(), now=NOW, local_hour=14)
    assert out is not None
    surfaced = [e for e in store.all_events() if e.state is EventState.SURFACED]
    assert len(surfaced) == 1  # only one surfaced this turn


# -- capture_reply + feedback -----------------------------------------------

def test_capture_reply_acks_and_returns(store):
    store.add_event(_ended_event())
    store.mark_surfaced("e1", NOW)
    captured = surface.capture_reply(store, "It was great, met three founders!", NOW + 10)
    assert captured == ("the meetup", "It was great, met three founders!")
    assert store.get("e1").state is EventState.ACKED


def test_capture_reply_none_when_nothing_awaiting(store):
    store.add_event(_ended_event())  # still TRACKED, not surfaced
    assert surface.capture_reply(store, "hi", NOW) is None


def test_capture_reply_ignores_stale_checkin(store):
    # Surfaced long ago -> a later unrelated message must NOT be captured.
    store.add_event(_ended_event())
    store.mark_surfaced("e1", NOW)
    later = NOW + 3 * HOUR  # beyond the 2h reply window
    assert surface.capture_reply(store, "totally unrelated question", later) is None
    # the check-in is left awaiting (not ACKed)
    assert store.get("e1").state is EventState.SURFACED


def test_apply_feedback_too_many_steps_cap_down(tmp_path):
    cfg = _cfg(push_cap_per_day=3)
    tuning = CadenceTuning(push_cap=3)
    new = surface.apply_feedback(cfg, tuning, tmp_path, "please stop reminding me so much", NOW)
    assert effective_push_cap(cfg.push_cap_per_day, new) == 2
    # persisted
    assert load_cadence(tmp_path).push_cap == 2


def test_apply_feedback_mute_adds_keyword(tmp_path):
    cfg = _cfg()
    new = surface.apply_feedback(cfg, CadenceTuning(), tmp_path, "mute the calendar reminders", NOW)
    assert "calendar" in new.muted_keywords


def test_apply_feedback_none_signal_is_noop(tmp_path):
    cfg = _cfg()
    t = CadenceTuning(push_cap=2)
    new = surface.apply_feedback(cfg, t, tmp_path, "what's for lunch?", NOW)
    assert new == t
