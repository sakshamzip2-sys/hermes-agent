"""Tests for the SENSE→DREAM link (outcome-tuned dreaming threshold)."""

from __future__ import annotations

from plugins.dreaming.outcome_link import adjusted_score_threshold

BASE = 0.65


def test_no_scores_returns_base() -> None:
    assert adjusted_score_threshold(BASE, scores_provider=lambda: []) == BASE


def test_too_little_history_returns_base() -> None:
    # < 10 scores is noise — no adjustment even if they're all high.
    assert adjusted_score_threshold(BASE, scores_provider=lambda: [0.9] * 5) == BASE


def test_good_outcomes_relax_the_bar() -> None:
    # 50 strong turns → lower the threshold (let more in).
    adj = adjusted_score_threshold(BASE, scores_provider=lambda: [0.9] * 50)
    assert adj < BASE
    assert abs(adj - (BASE - 0.05)) < 1e-9


def test_poor_outcomes_tighten_the_bar() -> None:
    # 100 weak turns → raise the threshold (be stricter).
    adj = adjusted_score_threshold(BASE, scores_provider=lambda: [0.5] * 100)
    assert adj > BASE
    assert abs(adj - (BASE + 0.05)) < 1e-9


def test_result_is_clamped_to_unit_interval() -> None:
    assert 0.0 <= adjusted_score_threshold(0.98, scores_provider=lambda: [0.0] * 100) <= 1.0
    assert 0.0 <= adjusted_score_threshold(0.02, scores_provider=lambda: [1.0] * 100) <= 1.0


def test_provider_error_is_fail_soft() -> None:
    def boom():
        raise RuntimeError("outcomes store unreadable")

    assert adjusted_score_threshold(BASE, scores_provider=boom) == BASE


def test_default_provider_failsoft_when_outcomes_absent() -> None:
    # With no scores_provider it reads the real store; in a clean env that's empty → base.
    assert adjusted_score_threshold(BASE) == BASE
