"""Tests for the pure composite per-turn scorer (outcomes plugin).

No network, no host imports — pure arithmetic, so every weight path is exercised
deterministically. Faithful to v1's anti-reward-hacking semantics.
"""

from __future__ import annotations

from plugins.outcomes.composite import compute_composite_score


def _score(**overrides) -> float:
    base = dict(
        tool_call_count=0,
        tool_success_count=0,
        tool_error_count=0,
        self_cancel_count=0,
        retry_count=0,
        conversation_abandoned=False,
        affirmation_present=False,
        correction_present=False,
        vibe_delta=0,
        standing_order_violation_count=0,
    )
    base.update(overrides)
    return compute_composite_score(**base)


def test_silent_turn_anchors_to_baseline() -> None:
    # No signals at all → 0.5 baseline (silence must not crash to zero).
    assert _score() == 0.5


def test_tool_success_raises_score_but_is_capped() -> None:
    s = _score(tool_success_count=10, tool_error_count=0)
    assert s > 0.5
    # Tool-success contribution is capped at 0.20 above baseline.
    assert s <= 0.5 + 0.20 + 1e-9


def test_correction_penalises_more_than_affirmation_rewards() -> None:
    affirmed = _score(affirmation_present=True)
    corrected = _score(correction_present=True)
    assert affirmed > 0.5
    assert corrected < 0.5
    # Asymmetry: a correction's penalty magnitude exceeds an affirmation's reward.
    assert (0.5 - corrected) > (affirmed - 0.5)


def test_self_cancel_and_retry_lower_the_score() -> None:
    assert _score(self_cancel_count=2) < 0.5
    assert _score(retry_count=3) < 0.5


def test_abandonment_is_a_modest_negative() -> None:
    s = _score(conversation_abandoned=True)
    assert s < 0.5
    # Modest: abandonment alone shouldn't swing more than a correction.
    assert (0.5 - s) <= 0.15 + 1e-9


def test_standing_order_violation_lowers_score() -> None:
    assert _score(standing_order_violation_count=3) < 0.5


def test_vibe_delta_nudges_symmetrically() -> None:
    up = _score(vibe_delta=1)
    down = _score(vibe_delta=-1)
    assert up > 0.5 > down
    assert abs((up - 0.5) - (0.5 - down)) < 1e-9


def test_score_is_clamped_to_unit_interval() -> None:
    worst = _score(
        self_cancel_count=99,
        retry_count=99,
        conversation_abandoned=True,
        correction_present=True,
        standing_order_violation_count=99,
        vibe_delta=-1,
    )
    best = _score(tool_success_count=99, affirmation_present=True, vibe_delta=1)
    assert 0.0 <= worst <= 1.0
    assert 0.0 <= best <= 1.0
    assert worst == 0.0  # everything bad saturates the floor
