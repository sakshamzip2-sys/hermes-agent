"""Tests for the ProactiveMoment model + the deterministic moment gate."""

from __future__ import annotations

from plugins.proactivity.models import Sensitivity
from plugins.proactivity.moment import Category, MomentState, ProactiveMoment
from plugins.proactivity.moment_gate import (
    URGENT_THRESHOLD,
    Decision,
    GateInputs,
    decide,
    score_moment,
)


def _m(**over) -> ProactiveMoment:
    base = dict(
        id="m1", source_id="commitment", category=Category.COMMITMENT,
        title="email Sam", body="You said you'd email Sam.", urgency=0.7,
        sensitivity=Sensitivity.USER_LOOP, confidence=0.9,
    )
    base.update(over)
    return ProactiveMoment(**base)


def _gi(**over) -> GateInputs:
    base = dict(in_active_conversation=False, in_quiet_hours=False, pushes_today=0,
                push_cap=3, now=1000.0, min_motivation=3)
    base.update(over)
    return GateInputs(**base)


# -- model ------------------------------------------------------------------

def test_dedup_key_stable():
    m = _m(dedup_key="")
    k1 = m.ensure_dedup_key()
    k2 = m.ensure_dedup_key()
    assert k1 == k2 and len(k1) == 16


def test_row_roundtrip():
    m = _m()
    m.ensure_dedup_key()
    back = ProactiveMoment.from_row(m.to_row())
    assert back.category is Category.COMMITMENT
    assert back.title == m.title
    assert back.sensitivity is Sensitivity.USER_LOOP


def test_push_eligible_respects_sensitivity_and_category():
    assert _m(sensitivity=Sensitivity.USER_LOOP, category=Category.COMMITMENT).push_eligible
    assert not _m(sensitivity=Sensitivity.INFERRED_SELF).push_eligible  # not push-eligible sensitivity
    assert not _m(category=Category.RE_ENGAGEMENT, sensitivity=Sensitivity.TOLD_FACT).push_eligible  # forbidden category


# -- score ------------------------------------------------------------------

def test_score_bounds_and_category_weight():
    assert score_moment(_m(category=Category.DEADLINE, urgency=0.9)) == 5
    assert 1 <= score_moment(_m(category=Category.RE_ENGAGEMENT, urgency=0.1)) <= 2
    assert score_moment(_m(category=Category.SUGGESTION, confidence=0.2)) >= 1


# -- gate decisions ---------------------------------------------------------

def test_sensitive_dropped():
    assert decide(_m(sensitivity=Sensitivity.SENSITIVE), _gi()) is Decision.DROP


def test_expired_dropped():
    assert decide(_m(expires_at=500.0), _gi(now=1000.0)) is Decision.DROP


def test_not_yet_triggered_dropped():
    assert decide(_m(trigger_at=2000.0), _gi(now=1000.0)) is Decision.DROP


def test_in_context_injects():
    assert decide(_m(), _gi(in_active_conversation=True)) is Decision.INJECT


def test_background_urgent_push_eligible_pushes():
    assert decide(_m(urgency=URGENT_THRESHOLD), _gi(in_active_conversation=False)) is Decision.PUSH


def test_background_over_budget_digests():
    assert decide(_m(urgency=0.9), _gi(pushes_today=3, push_cap=3)) is Decision.DIGEST


def test_quiet_hours_no_push():
    assert decide(_m(urgency=0.9), _gi(in_quiet_hours=True)) is Decision.DIGEST


def test_non_push_eligible_digests_out_of_band():
    # re-engagement is push-forbidden -> digest, never push, even out of band
    assert decide(_m(category=Category.RE_ENGAGEMENT, urgency=0.9), _gi()) is Decision.DIGEST


def test_low_urgency_low_score_digests_not_drops():
    # below motivation but low-urgency informational -> rides the digest
    m = _m(category=Category.HABIT, urgency=0.1, confidence=0.4)
    assert decide(m, _gi(min_motivation=5)) is Decision.DIGEST


def test_below_motivation_higher_urgency_drops():
    m = _m(category=Category.SUGGESTION, urgency=0.5, confidence=0.3)
    assert decide(m, _gi(min_motivation=5)) is Decision.DROP


def test_push_forbidden_category_never_pushes_even_when_urgent():
    for _ in range(3):
        d = decide(_m(category=Category.RE_ENGAGEMENT, urgency=1.0,
                      sensitivity=Sensitivity.TOLD_FACT), _gi())
        assert d is not Decision.PUSH
