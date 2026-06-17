"""Tests for the DREAM→EVOLVE candidate extractor (injected aux-LLM, no network)."""

from __future__ import annotations

import asyncio

from plugins.playbook_synthesizer.extractor import extract_candidates


def _run(coro):
    return asyncio.run(coro)


def _extract(reply, facts=("fact one", "fact two")):
    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return reply

    return _run(extract_candidates(list(facts), chat_fn=chat_fn))


def test_parses_valid_json_array() -> None:
    reply = (
        '[{"name": "Deploy widget", "description": "when deploying", '
        '"steps": ["migrate", "flip flag", "smoke test"], "recurrence": 3}]'
    )
    cands = _extract(reply)
    assert len(cands) == 1
    assert cands[0].name == "Deploy widget"
    assert cands[0].steps == ["migrate", "flip flag", "smoke test"]
    assert cands[0].recurrence == 3


def test_tolerates_prose_around_json() -> None:
    reply = 'Sure! Here you go:\n[{"name":"X","description":"d","steps":["a","b"]}]\nHope that helps.'
    cands = _extract(reply)
    assert len(cands) == 1 and cands[0].name == "X"


def test_empty_array_yields_nothing() -> None:
    assert _extract("[]") == []


def test_unparseable_yields_nothing() -> None:
    assert _extract("I could not find any reusable procedures.") == []


def test_drops_items_with_fewer_than_two_steps() -> None:
    reply = '[{"name":"thin","description":"d","steps":["only one"]}]'
    assert _extract(reply) == []


def test_no_facts_returns_empty_without_calling_llm() -> None:
    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not call the LLM with no facts")

    assert _run(extract_candidates([], chat_fn=boom)) == []


def test_chat_fn_none_is_fail_soft() -> None:
    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return None

    assert _run(extract_candidates(["f"], chat_fn=chat_fn)) == []


def test_chat_fn_raise_is_fail_soft() -> None:
    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        raise RuntimeError("provider down")

    assert _run(extract_candidates(["f"], chat_fn=chat_fn)) == []
