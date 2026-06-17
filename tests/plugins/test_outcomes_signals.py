"""Tests for per-turn signal accumulation + NL heuristics (outcomes plugin)."""

from __future__ import annotations

from plugins.outcomes.signals import TurnSignals, detect_affirmation, detect_correction


def test_detect_clear_correction() -> None:
    assert detect_correction("no, that's wrong") is True
    assert detect_correction("That's not what I asked for") is True
    assert detect_correction("actually, you misunderstood") is True


def test_detect_correction_is_precise_not_trigger_happy() -> None:
    # Neutral follow-ups must NOT be flagged as corrections (precision over recall).
    assert detect_correction("ok thanks, now do the next part") is False
    assert detect_correction("can you also add a test?") is False
    assert detect_correction("") is False


def test_detect_affirmation() -> None:
    assert detect_affirmation("perfect, thank you!") is True
    assert detect_affirmation("that's exactly right") is True
    assert detect_affirmation("great work") is True


def test_affirmation_not_triggered_by_neutral() -> None:
    assert detect_affirmation("do the next thing") is False
    assert detect_affirmation("") is False


def test_turn_signals_accumulate_tool_outcomes() -> None:
    sig = TurnSignals()
    sig.record_tool(success=True)
    sig.record_tool(success=True)
    sig.record_tool(success=False)
    kw = sig.to_score_kwargs()
    assert kw["tool_success_count"] == 2
    assert kw["tool_error_count"] == 1
    assert kw["tool_call_count"] == 3


def test_to_score_kwargs_has_all_composite_keys() -> None:
    # Must supply every kwarg compute_composite_score requires.
    from plugins.outcomes.composite import compute_composite_score

    kw = TurnSignals().to_score_kwargs()
    # Should not raise — every required keyword is present.
    score = compute_composite_score(**kw)
    assert score == 0.5  # empty signals → baseline


def test_correction_and_affirmation_flow_into_kwargs() -> None:
    sig = TurnSignals()
    sig.apply_user_followup("no, that's wrong")
    kw = sig.to_score_kwargs()
    assert kw["correction_present"] is True
    assert kw["affirmation_present"] is False
