"""Tests for the outcomes engine orchestration (real temp store, injected judge)."""

from __future__ import annotations

from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.store import OutcomesStore


def _engine(tmp_path, **kw):
    return OutcomesEngine(OutcomesStore(tmp_path / "o.db"), **kw)


def test_finalize_no_judge_records_composite(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.record_tool("S", success=True)
    score = eng.finalize_turn("S", 1, now=1.0)
    # No judge → fused == composite; 2 successes raise it above baseline.
    assert score > 0.5
    assert eng.store.recent_turn_scores() == [score]


def test_finalize_with_judge_fuses(tmp_path) -> None:
    eng = _engine(tmp_path, judge_enabled=True)

    def judge_fn(*, trajectory_summary, composite_score, standing_orders):  # noqa: ANN001
        return 1.0  # perfect judge verdict

    score = eng.finalize_turn("S", 1, now=1.0, judge_fn=judge_fn)
    # composite (baseline 0.5, no signals) fused with judge 1.0 → 0.4*0.5 + 0.6*1.0 = 0.8
    assert abs(score - 0.8) < 1e-9


def test_user_followup_correction_lowers_score(tmp_path) -> None:
    eng = _engine(tmp_path)
    score = eng.finalize_turn("S", 1, user_followup="no, that's wrong", now=1.0)
    assert score < 0.5


def test_signals_reset_between_turns(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.finalize_turn("S", 1, now=1.0)
    # Second turn starts fresh — no carried-over tool successes.
    score2 = eng.finalize_turn("S", 2, now=2.0)
    assert score2 == 0.5  # baseline, signals were reset


def test_sessions_are_isolated(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("A", success=False)
    eng.record_tool("A", success=False)
    # Session B has no signals; its turn should be baseline.
    score_b = eng.finalize_turn("B", 1, now=1.0)
    assert score_b == 0.5


def test_run_cycle_returns_summary(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.finalize_turn("S", 1, now=1.0)
    eng.finalize_turn("S", 2, now=2.0)
    summary = eng.run_cycle()
    assert summary["recorded"] >= 0
    assert "mean_recent" in summary


def test_judge_disabled_ignores_judge_fn(tmp_path) -> None:
    eng = _engine(tmp_path, judge_enabled=False)

    def judge_fn(**kw):  # noqa: ANN003
        raise AssertionError("judge must not be called when disabled")

    score = eng.finalize_turn("S", 1, now=1.0, judge_fn=judge_fn)
    assert score == 0.5  # composite-only baseline
