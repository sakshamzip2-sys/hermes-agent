"""Tests for synthesize_from_facts — the production DREAM→EVOLVE bridge."""

from __future__ import annotations

import asyncio

from plugins.playbook_synthesizer import synthesize_from_facts
from plugins.playbook_synthesizer.config import PlaybookConfig


def _cfg(enabled=True):
    return PlaybookConfig(enabled=enabled, max_per_cycle=3, category="learned")


def _run(coro):
    return asyncio.run(coro)


def test_bridge_disabled_is_noop() -> None:
    out = _run(synthesize_from_facts(["f"], config=_cfg(enabled=False),
                                     creator_fn=lambda **k: "x", exists_fn=lambda n: False))
    assert out["reason"] == "disabled"
    assert out["created"] == []


def test_bridge_extracts_then_synthesizes() -> None:
    made = []

    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return (
            '[{"name":"Recover the gateway","description":"when the gateway crash-loops",'
            '"steps":["git merge --abort","uv sync","restart daemon"],"recurrence":4}]'
        )

    out = _run(synthesize_from_facts(
        ["gateway crash-looped after a bad merge", "same fix worked twice"],
        chat_fn=chat_fn,
        creator_fn=lambda *, name, content, category: made.append(name) or "ok",
        exists_fn=lambda n: False,
        config=_cfg(),
    ))
    assert out["created"] == ["recover-the-gateway"]
    assert made == ["recover-the-gateway"]


def test_bridge_noop_when_extractor_finds_nothing() -> None:
    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        return "[]"

    out = _run(synthesize_from_facts(
        ["a one-off fact"], chat_fn=chat_fn,
        creator_fn=lambda **k: "x", exists_fn=lambda n: False, config=_cfg(),
    ))
    assert out["created"] == []
