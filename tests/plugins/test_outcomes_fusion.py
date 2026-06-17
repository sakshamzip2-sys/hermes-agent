"""Tests for the pure turn_score fusion (outcomes plugin)."""

from __future__ import annotations

from plugins.outcomes.fusion import fused_turn_score, is_judge_disagreement


def test_judge_absent_falls_back_to_composite() -> None:
    assert fused_turn_score(0.73, None) == 0.73


def test_weighted_combination_when_judge_present() -> None:
    # 0.4 * composite + 0.6 * judge
    assert abs(fused_turn_score(0.5, 1.0) - 0.8) < 1e-9
    assert abs(fused_turn_score(1.0, 0.0) - 0.4) < 1e-9


def test_disagreement_flag() -> None:
    assert is_judge_disagreement(0.1, 0.9) is True
    assert is_judge_disagreement(0.5, 0.6) is False


def test_no_disagreement_when_judge_absent() -> None:
    assert is_judge_disagreement(0.5, None) is False
