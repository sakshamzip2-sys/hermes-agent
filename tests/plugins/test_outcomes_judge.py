"""Tests for the aux-LLM judge (outcomes plugin). Injected fake chat_fn — no network."""

from __future__ import annotations

import asyncio

from plugins.outcomes.judge import JudgeVerdict, score_turn_via_judge


def _run(coro):
    return asyncio.run(coro)


def _judge(reply, **over):
    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return reply

    kwargs = dict(
        trajectory_summary="agent ran 2 tools, both succeeded",
        composite_score=0.7,
        standing_orders="",
        chat_fn=chat_fn,
        model="fake-model",
    )
    kwargs.update(over)
    return _run(score_turn_via_judge(**kwargs))


def test_valid_response_parses_verdict() -> None:
    v = _judge("<judge_score>0.82</judge_score><reasoning>good turn</reasoning>")
    assert isinstance(v, JudgeVerdict)
    assert abs(v.judge_score - 0.82) < 1e-9
    assert v.judge_reasoning == "good turn"
    assert v.judge_model == "fake-model"


def test_unparseable_response_returns_none() -> None:
    assert _judge("I think it was pretty good honestly") is None


def test_out_of_range_score_returns_none() -> None:
    assert _judge("<judge_score>1.7</judge_score>") is None


def test_chat_fn_returns_none_yields_none() -> None:
    assert _judge(None) is None


def test_chat_fn_raising_yields_none() -> None:
    async def boom(system, user, *, max_tokens):  # noqa: ANN001
        raise RuntimeError("provider exploded")

    v = _run(
        score_turn_via_judge(
            trajectory_summary="x",
            composite_score=0.5,
            standing_orders="",
            chat_fn=boom,
            model="fake",
        )
    )
    assert v is None


def test_reasoning_optional() -> None:
    v = _judge("<judge_score>0.5</judge_score>")
    assert isinstance(v, JudgeVerdict)
    assert v.judge_reasoning == ""
