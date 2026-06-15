"""Tests for dreaming config loading and the LLM-adapter offline behaviour."""

from __future__ import annotations

import asyncio

from plugins.dreaming import llm
from plugins.dreaming.config import DEFAULTS, load_dreaming_config


def _run(coro):
    return asyncio.run(coro)


# -- config -----------------------------------------------------------------

def test_config_defaults_match_v1():
    cfg = load_dreaming_config({})
    assert cfg.enabled is True
    assert cfg.engine.score_threshold == 0.65
    assert cfg.engine.min_recall_count == 2
    assert cfg.engine.diversity_threshold == 0.8
    assert cfg.engine.max_promotions_per_run == 20
    assert cfg.min_interval_hours == DEFAULTS["min_interval_hours"]


def test_config_overrides_applied():
    cfg = load_dreaming_config(
        {"enabled": False, "score_threshold": 0.9, "min_recall_count": 5,
         "min_interval_hours": 12}
    )
    assert cfg.enabled is False
    assert cfg.engine.score_threshold == 0.9
    assert cfg.engine.min_recall_count == 5
    assert cfg.min_interval_seconds == 12 * 3600


def test_config_bad_types_fall_back():
    cfg = load_dreaming_config({"score_threshold": "not-a-number", "min_recall_count": "x"})
    assert cfg.engine.score_threshold == 0.65
    assert cfg.engine.min_recall_count == 2


# -- lexical embed ----------------------------------------------------------

def test_lexical_embed_identical_texts_cosine_one():
    vecs = _run(llm.lexical_embed(["rust is great", "rust is great"]))
    # both normalized & identical -> dot == 1.0
    dot = sum(a * b for a, b in zip(vecs[0], vecs[1]))
    assert abs(dot - 1.0) < 1e-9


def test_lexical_embed_disjoint_texts_cosine_zero():
    vecs = _run(llm.lexical_embed(["alpha beta", "gamma delta"]))
    dot = sum(a * b for a, b in zip(vecs[0], vecs[1]))
    assert abs(dot) < 1e-9


def test_lexical_embed_aligned_dimensions():
    vecs = _run(llm.lexical_embed(["a b c", "b c d", "x y z"]))
    assert len({len(v) for v in vecs}) == 1  # all same dimension


# -- text tasks degrade safely without a provider ---------------------------

def test_extract_facts_no_provider_returns_empty(monkeypatch):
    async def _no_client(_s, _u, *, max_tokens, temperature=0.0):
        return None

    monkeypatch.setattr(llm, "_aux_chat", _no_client)
    assert _run(llm.extract_facts("some transcript")) == []


def test_score_fact_no_provider_returns_zero(monkeypatch):
    async def _no_client(_s, _u, *, max_tokens, temperature=0.0):
        return None

    monkeypatch.setattr(llm, "_aux_chat", _no_client)
    assert _run(llm.score_fact("a fact")) == 0.0


def test_score_fact_parses_number(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return "0.82"

    monkeypatch.setattr(llm, "_aux_chat", _client)
    assert _run(llm.score_fact("a fact")) == 0.82


def test_score_fact_clamps(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return "1.7"

    monkeypatch.setattr(llm, "_aux_chat", _client)
    assert _run(llm.score_fact("a fact")) == 1.0


def test_extract_facts_parses_lines(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return "- Likes Rust.\n- Lives in Berlin.\n2. Works at Acme."

    monkeypatch.setattr(llm, "_aux_chat", _client)
    facts = _run(llm.extract_facts("transcript"))
    assert facts == ["Likes Rust.", "Lives in Berlin.", "Works at Acme."]


def test_extract_facts_none_sentinel(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return "NONE"

    monkeypatch.setattr(llm, "_aux_chat", _client)
    assert _run(llm.extract_facts("transcript")) == []


def test_decide_supersede_parses(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return "UPDATE"

    monkeypatch.setattr(llm, "_aux_chat", _client)
    assert _run(llm.decide_supersede("new", "old")) == "UPDATE"


def test_decide_supersede_defaults_noop(monkeypatch):
    async def _client(_s, _u, *, max_tokens, temperature=0.0):
        return None

    monkeypatch.setattr(llm, "_aux_chat", _client)
    assert _run(llm.decide_supersede("new", "old")) == "NOOP"
