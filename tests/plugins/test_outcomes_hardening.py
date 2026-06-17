"""Hardening tests from the adversarial review — false positives, leaks, races, judge wiring."""

from __future__ import annotations

from plugins.outcomes import hooks
from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.signals import TurnSignals, detect_correction
from plugins.outcomes.store import OutcomesStore


# --- Finding: regex false positives ----------------------------------------
def test_not_bad_is_not_a_correction() -> None:
    # "that's not bad" is mild praise, NOT a correction — must not tank the score.
    assert detect_correction("that's not bad") is False
    assert detect_correction("that's not a problem, thanks") is False
    assert detect_correction("not wrong at all, nice") is False


def test_real_corrections_still_detected() -> None:
    assert detect_correction("no, that's wrong") is True
    assert detect_correction("that's not what I asked for") is True
    assert detect_correction("that's incorrect") is True
    assert detect_correction("you misunderstood the task") is True


def test_mixed_feedback_prefers_correction() -> None:
    sig = TurnSignals()
    sig.apply_user_followup("no, that's not what I meant, but thanks for the effort")
    kw = sig.to_score_kwargs()
    assert kw["correction_present"] is True
    assert kw["affirmation_present"] is False
    assert kw["vibe_delta"] == -1


# --- Finding: incomplete failure statuses ----------------------------------
def test_timeout_and_skipped_are_failures() -> None:
    assert hooks.tool_call_succeeded(status="timeout") is False
    assert hooks.tool_call_succeeded(status="skipped") is False
    assert hooks.tool_call_succeeded(status="incomplete") is False


# --- Finding: unbounded per-session state growth ---------------------------
def test_session_state_is_lru_bounded(tmp_path) -> None:
    eng = OutcomesEngine(OutcomesStore(tmp_path / "o.db"))
    eng.max_sessions = 8  # shrink the cap for the test
    for i in range(50):
        eng.record_tool(f"sess-{i}", success=True)  # 50 distinct abandoned sessions
    # Live-signal dict must not grow without bound.
    assert len(eng._signals) <= 8


def test_pending_state_is_lru_bounded(tmp_path) -> None:
    eng = OutcomesEngine(OutcomesStore(tmp_path / "o.db"))
    eng.max_sessions = 8
    for i in range(50):
        eng.record_tool(f"sess-{i}", success=True)
        eng.stage_turn(f"sess-{i}", 1)
    assert len(eng._pending) <= 8


# --- Finding: judge exception fallback was untested ------------------------
def test_judge_exception_falls_back_to_composite(tmp_path) -> None:
    eng = OutcomesEngine(OutcomesStore(tmp_path / "o.db"), judge_enabled=True)

    def boom(**kw):  # noqa: ANN003
        raise RuntimeError("provider down")

    score = eng.finalize_turn("S", 1, now=1.0, judge_fn=boom)
    assert score == 0.5  # composite baseline; judge failure swallowed


# --- Finding: judge dead in hook path → judge runs in the async cycle ------
def test_rejudge_recent_applies_judge_to_unjudged_turns(tmp_path) -> None:
    import asyncio

    store = OutcomesStore(tmp_path / "o.db")
    eng = OutcomesEngine(store, judge_enabled=True)
    # Two composite-only turns recorded by the per-turn path.
    eng.finalize_turn("S", 1, now=1.0)  # composite 0.5, judge None
    eng.finalize_turn("S", 2, now=2.0)

    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return "<judge_score>1.0</judge_score>"

    n = asyncio.run(eng.rejudge_recent(limit=10, chat_fn=chat_fn, now=3.0))
    assert n == 2  # both turns re-judged
    # Re-judged scores fuse composite(0.5) with judge(1.0) → 0.8, newest-first.
    scores = store.recent_turn_scores(limit=2)
    assert all(abs(s - 0.8) < 1e-9 for s in scores)


def test_rejudge_noop_when_judge_disabled(tmp_path) -> None:
    import asyncio

    eng = OutcomesEngine(OutcomesStore(tmp_path / "o.db"), judge_enabled=False)
    eng.finalize_turn("S", 1, now=1.0)

    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        raise AssertionError("judge must not run when disabled")

    n = asyncio.run(eng.rejudge_recent(limit=10, chat_fn=chat_fn, now=2.0))
    assert n == 0
