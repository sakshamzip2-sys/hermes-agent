"""Tests for the one-turn-delayed staged scoring (the production hook path).

The user's correction of turn N arrives as the message starting turn N+1, so turn N
must be scored when that feedback lands — not immediately.
"""

from __future__ import annotations

from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.store import OutcomesStore


def _engine(tmp_path, **kw):
    return OutcomesEngine(OutcomesStore(tmp_path / "o.db"), **kw)


def test_stage_then_resolve_scores_prior_turn(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 1)
    # Nothing scored yet — the turn is pending feedback.
    assert eng.store.count() == 0
    # Next message arrives (its feedback). Positive → does not lower the tool-success score.
    score = eng.resolve_pending("S", user_followup="perfect, thanks!", now=1.0)
    assert score is not None and score > 0.5
    assert eng.store.count() == 1


def test_correction_lands_on_the_turn_it_critiques(tmp_path) -> None:
    eng = _engine(tmp_path)
    # Turn 1: a perfect-looking tool run.
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 1)
    # Turn 2 begins with a correction of turn 1 → turn 1 should be penalised.
    score1 = eng.resolve_pending("S", user_followup="no, that's wrong", now=1.0)
    assert score1 is not None and score1 < 0.5


def test_resolve_with_no_pending_returns_none(tmp_path) -> None:
    eng = _engine(tmp_path)
    assert eng.resolve_pending("S", user_followup="hi") is None


def test_flush_pending_scores_last_turn_without_feedback(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 1)
    score = eng.flush_pending("S", now=1.0)
    assert score is not None and score > 0.5
    assert eng.store.count() == 1


def test_staging_a_new_turn_flushes_an_unresolved_prior_turn(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 1)              # turn 1 pending
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 2)              # no feedback for turn 1 → it gets flushed
    assert eng.store.count() == 1       # turn 1 flushed
    eng.flush_pending("S", now=2.0)     # turn 2 flushed at end
    assert eng.store.count() == 2


def test_live_signals_reset_after_staging(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("S", success=True)
    eng.stage_turn("S", 1)
    # Turn 2 accumulates fresh — no carryover.
    eng.stage_turn("S", 2)  # flushes turn 1 (success>baseline)
    eng.flush_pending("S", now=2.0)  # turn 2 had no signals → baseline
    scores = eng.store.recent_turn_scores()
    assert scores[0] == 0.5  # newest (turn 2) baseline
    assert scores[1] > 0.5   # turn 1 had the tool success
