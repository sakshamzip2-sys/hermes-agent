"""Tests for the deterministic proactivity gate + renderers + model invariants."""

from __future__ import annotations

from plugins.proactivity.gate import (
    IN_CONTEXT_GRACE_HOURS,
    GateInputs,
    decide_tier,
    render_checkin,
    render_register_assist,
)
from plugins.proactivity.models import (
    EventContext,
    Sensitivity,
    SurfaceTier,
    TrackedEvent,
)


def _inputs(**over) -> GateInputs:
    base = dict(
        sensitivity=Sensitivity.TOLD_FACT,
        in_quiet_hours=False,
        pushes_in_window=0,
        push_cap=1,
        hours_since_end=IN_CONTEXT_GRACE_HOURS + 1,
        opened_conversation_since_end=False,
        enabled=True,
    )
    base.update(over)
    return GateInputs(**base)


def test_disabled_is_silent():
    assert decide_tier(_inputs(enabled=False)) is SurfaceTier.SILENT


def test_sensitive_is_hard_suppressed():
    assert decide_tier(_inputs(sensitivity=Sensitivity.SENSITIVE)) is SurfaceTier.SILENT


def test_push_when_all_conditions_met():
    assert decide_tier(_inputs()) is SurfaceTier.PUSH


def test_no_push_before_grace_lapses():
    assert decide_tier(_inputs(hours_since_end=1.0)) is SurfaceTier.IN_CONTEXT


def test_no_push_if_conversation_opened():
    assert decide_tier(_inputs(opened_conversation_since_end=True)) is SurfaceTier.IN_CONTEXT


def test_no_push_in_quiet_hours():
    assert decide_tier(_inputs(in_quiet_hours=True)) is SurfaceTier.IN_CONTEXT


def test_no_push_over_cap():
    assert decide_tier(_inputs(pushes_in_window=1, push_cap=1)) is SurfaceTier.IN_CONTEXT


def test_no_push_for_non_push_eligible_sensitivity():
    # INVARIANT 1: only TOLD_FACT / USER_LOOP may push.
    for s in (Sensitivity.INFERRED_SELF, Sensitivity.RELATIONAL, Sensitivity.THIRD_PARTY):
        assert decide_tier(_inputs(sensitivity=s)) is SurfaceTier.IN_CONTEXT


def test_user_loop_is_push_eligible():
    assert decide_tier(_inputs(sensitivity=Sensitivity.USER_LOOP)) is SurfaceTier.PUSH


# -- model invariants -------------------------------------------------------

def test_push_eligible_property():
    assert Sensitivity.TOLD_FACT.push_eligible is True
    assert Sensitivity.USER_LOOP.push_eligible is True
    assert Sensitivity.INFERRED_SELF.push_eligible is False
    assert Sensitivity.SENSITIVE.push_eligible is False


def test_event_row_roundtrip():
    ev = TrackedEvent(
        id="e1", title="Concert", starts_at=1.0, ends_at=2.0, source="user_told",
        sensitivity=Sensitivity.USER_LOOP, attended_confirmed=True, created_at=0.5,
    )
    back = TrackedEvent.from_row(ev.to_row())
    assert back == ev


# -- renderers --------------------------------------------------------------

def _ev(title="the meetup", attended=False):
    return TrackedEvent(id="x", title=title, starts_at=0, ends_at=0,
                        source="user_told", attended_confirmed=attended)


def test_render_checkin_assume_nothing_without_attendance():
    out = render_checkin(_ev(attended=False))
    assert "did you make it to the meetup" in out


def test_render_checkin_warm_when_attended():
    out = render_checkin(_ev(attended=True))
    assert "the meetup go" in out.lower()


def test_render_checkin_uses_name():
    out = render_checkin(_ev(attended=True), EventContext(name="Sam"))
    assert out.startswith("Sam,")


def test_render_register_assist_taste_claim_only_with_history():
    no_hist = render_register_assist(_ev(), EventContext(name="Sam", has_history=False))
    with_hist = render_register_assist(_ev(), EventContext(name="Sam", has_history=True))
    assert "your kind of thing" not in no_hist
    assert "your kind of thing" in with_hist
