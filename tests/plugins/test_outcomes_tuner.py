"""Tests for the pure outcome-driven threshold modulator (outcomes plugin)."""

from __future__ import annotations

from plugins.outcomes.tuner import OutcomeAdjustmentConfig, compute_outcome_adjustment

BASE = 0.65


def test_empty_history_no_adjustment() -> None:
    assert compute_outcome_adjustment([], baseline_threshold=BASE) == 0.0


def test_recent_window_above_baseline_relaxes() -> None:
    # Newest window mean clearly above baseline → negative delta (lower the bar).
    scores = [0.9] * 50
    delta = compute_outcome_adjustment(scores, baseline_threshold=BASE)
    assert delta < 0.0
    assert delta == -0.05  # default down_step


def test_consecutive_low_windows_tighten() -> None:
    # Two full windows whose means sit below baseline - margin (0.60) → positive delta.
    scores = [0.5] * 100
    delta = compute_outcome_adjustment(scores, baseline_threshold=BASE)
    assert delta > 0.0
    assert delta == 0.05  # default up_step


def test_neutral_band_no_adjustment() -> None:
    # Mean between (baseline - margin)=0.60 and baseline=0.65 → neither relax nor tighten.
    scores = [0.62] * 100
    assert compute_outcome_adjustment(scores, baseline_threshold=BASE) == 0.0


def test_delta_is_always_clamped() -> None:
    cfg = OutcomeAdjustmentConfig(down_step=1.0, up_step=1.0, max_delta=0.05)
    relax = compute_outcome_adjustment([1.0] * 50, baseline_threshold=BASE, config=cfg)
    tighten = compute_outcome_adjustment([0.0] * 100, baseline_threshold=BASE, config=cfg)
    assert relax == -0.05
    assert tighten == 0.05


def test_single_low_window_below_min_low_windows_does_not_tighten() -> None:
    # Only one low window (need min_low_windows=2 by default) → no tightening.
    scores = [0.5] * 50
    assert compute_outcome_adjustment(scores, baseline_threshold=BASE) == 0.0
