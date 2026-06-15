"""Tests for the pure three-gate dreaming engine.

No network, no host imports — the engine takes injectable callables, so every
gate path is exercised deterministically. Async methods are driven with
``asyncio.run`` to avoid a pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio

import pytest

from plugins.dreaming.engine import (
    DreamCandidate,
    DreamingConfig,
    DreamingPipeline,
    DreamOutcome,
    _cosine,
    best_match_against,
)


def _cand(text: str, eid: str | None = None) -> DreamCandidate:
    return DreamCandidate(event_id=eid or text[:16], raw_text=text)


def _run(coro):
    return asyncio.run(coro)


class _Recorder:
    """Captures promote/hold/replace side effects."""

    def __init__(self):
        self.promoted: list[str] = []
        self.held: list[str] = []
        self.replaced: list[tuple[str, str]] = []

    def promote(self, text):
        self.promoted.append(text)

    def hold(self, text, max_bytes):
        self.held.append(text)

    def replace(self, old, new):
        self.replaced.append((old, new))
        return True


def _pipeline(rec, *, config=None, score=1.0, recall=5, embed=None, decision=None):
    async def score_fn(_text):
        if isinstance(score, Exception):
            raise score
        return score

    def recall_fn(_eid):
        return recall

    async def decision_fn(_new, _existing):
        return decision

    return DreamingPipeline(
        config or DreamingConfig(),
        score_fn=score_fn,
        recall_count_fn=recall_fn,
        promote_fn=rec.promote,
        hold_fn=rec.hold,
        embed_fn=embed,
        decision_fn=decision_fn if decision is not None else None,
        replace_fn=rec.replace,
    )


def test_all_gates_pass_promotes():
    rec = _Recorder()
    p = _pipeline(rec, score=0.9, recall=3)
    summary = _run(p.run_once([_cand("Lives in Berlin.")], existing_memories=[]))
    assert len(summary.promoted) == 1
    assert summary.promoted[0].outcome is DreamOutcome.PROMOTED
    assert rec.promoted == ["Lives in Berlin."]


def test_low_score_is_held():
    rec = _Recorder()
    p = _pipeline(rec, score=0.1, recall=3)  # below 0.65
    summary = _run(p.run_once([_cand("ephemeral")], existing_memories=[]))
    assert len(summary.held) == 1
    assert rec.held == ["ephemeral"]
    assert not rec.promoted


def test_low_recall_is_held():
    rec = _Recorder()
    p = _pipeline(rec, score=0.9, recall=1)  # below min_recall_count=2
    summary = _run(p.run_once([_cand("one-off")], existing_memories=[]))
    assert len(summary.held) == 1
    assert not rec.promoted


def test_recall_gate_disabled_promotes_despite_zero_recall():
    rec = _Recorder()
    cfg = DreamingConfig(recall_gate_enabled=False)
    p = _pipeline(rec, config=cfg, score=0.9, recall=0)
    summary = _run(p.run_once([_cand("Prefers dark mode.")], existing_memories=[]))
    assert len(summary.promoted) == 1


def test_diversity_fail_drops_without_decision_fn():
    rec = _Recorder()
    # Identical existing memory -> cosine 1.0 -> diversity fails. No decision_fn.
    async def embed(texts):
        # all identical vectors -> cosine 1.0
        return [[1.0, 0.0] for _ in texts]

    cfg = DreamingConfig(supersede_enabled=False)
    p = _pipeline(rec, config=cfg, score=0.9, recall=3, embed=embed)
    summary = _run(p.run_once([_cand("dup")], existing_memories=["dup"]))
    assert len(summary.dropped) == 1
    assert not rec.promoted


def test_supersede_update_replaces_entry():
    rec = _Recorder()
    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]  # cosine 1.0

    p = _pipeline(rec, score=0.9, recall=3, embed=embed, decision="UPDATE")
    summary = _run(
        p.run_once([_cand("Now uses pnpm.")], existing_memories=["Uses npm."])
    )
    assert len(summary.updated) == 1
    assert summary.updated[0].outcome is DreamOutcome.UPDATED
    assert rec.replaced == [("Uses npm.", "Now uses pnpm.")]
    assert summary.updated[0].old_text == "Uses npm."


def test_supersede_add_promotes_distinct_fact():
    rec = _Recorder()
    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]

    p = _pipeline(rec, score=0.9, recall=3, embed=embed, decision="ADD")
    summary = _run(
        p.run_once([_cand("Also likes Rust.")], existing_memories=["Likes Go."])
    )
    assert len(summary.promoted) == 1
    assert rec.promoted == ["Also likes Rust."]


def test_supersede_noop_drops():
    rec = _Recorder()
    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]

    p = _pipeline(rec, score=0.9, recall=3, embed=embed, decision="NOOP")
    summary = _run(p.run_once([_cand("dup")], existing_memories=["dup"]))
    assert len(summary.dropped) == 1
    assert not rec.promoted


def test_idempotency_skips_processed():
    rec = _Recorder()
    p = _pipeline(rec, score=0.9, recall=3)
    c = _cand("Lives in Berlin.", eid="abc123")
    summary = _run(
        p.run_once([c], existing_memories=[], already_processed_event_ids={"abc123"})
    )
    assert summary.skipped_already_processed == 1
    assert not rec.promoted


def test_promotion_cap_enforced():
    rec = _Recorder()
    cfg = DreamingConfig(max_promotions_per_run=2)
    p = _pipeline(rec, config=cfg, score=0.9, recall=3)
    cands = [_cand(f"fact {i}", eid=f"e{i}") for i in range(5)]
    summary = _run(p.run_once(cands, existing_memories=[]))
    assert len(summary.promoted) == 2
    assert len(rec.promoted) == 2


def test_rate_limit_halts_loop():
    rec = _Recorder()

    class RateLimitedError(Exception):
        pass

    p = _pipeline(rec, score=RateLimitedError("429"), recall=3)
    cands = [_cand(f"fact {i}", eid=f"e{i}") for i in range(3)]
    summary = _run(p.run_once(cands, existing_memories=[]))
    assert summary.rate_limited is True
    assert not rec.promoted


def test_score_exception_treated_as_zero():
    rec = _Recorder()
    p = _pipeline(rec, score=ValueError("boom"), recall=3)
    summary = _run(p.run_once([_cand("x")], existing_memories=[]))
    # score -> 0.0 -> below threshold -> held
    assert len(summary.held) == 1


def test_disabled_config_returns_empty():
    rec = _Recorder()
    cfg = DreamingConfig(enabled=False)
    p = _pipeline(rec, config=cfg, score=0.9, recall=3)
    summary = _run(p.run_once([_cand("x")], existing_memories=[]))
    assert summary.counts()["evaluated"] == 0
    assert not rec.promoted


def test_promote_failure_downgrades_to_held():
    rec = _Recorder()

    def bad_promote(_text):
        raise OSError("disk full")

    async def score_fn(_t):
        return 0.9

    p = DreamingPipeline(
        DreamingConfig(),
        score_fn=score_fn,
        recall_count_fn=lambda _e: 3,
        promote_fn=bad_promote,
        hold_fn=rec.hold,
    )
    summary = _run(p.run_once([_cand("x")], existing_memories=[]))
    assert len(summary.held) == 1


# -- cosine / best_match helpers --------------------------------------------

def test_cosine_basics():
    assert _cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine([], [1]) == 0.0
    assert _cosine([0, 0], [0, 0]) == 0.0


def test_best_match_no_embed_returns_novel():
    res = _run(best_match_against("x", ["a", "b"], embed_fn=None))
    assert res == (0.0, -1)


def test_best_match_finds_closest():
    async def embed(texts):
        # candidate matches second existing exactly
        mapping = {"cand": [1.0, 0.0], "a": [0.0, 1.0], "b": [1.0, 0.0]}
        return [mapping[t] for t in texts]

    cos, idx = _run(best_match_against("cand", ["a", "b"], embed_fn=embed))
    assert idx == 1
    assert cos == pytest.approx(1.0)
