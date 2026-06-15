"""Tests for the dreaming clustering pre-gate + semantic-embed fallback."""

from __future__ import annotations

import asyncio

from plugins.dreaming import llm
from plugins.dreaming.cluster import cluster_candidates
from plugins.dreaming.engine import DreamCandidate


def _run(coro):
    return asyncio.run(coro)


def _c(eid, text):
    return DreamCandidate(event_id=eid, raw_text=text)


async def _identical_embed(texts):
    return [[1.0, 0.0] for _ in texts]  # all identical -> cosine 1.0


async def _orthogonal_embed(texts):
    # each text gets its own basis vector -> cosine 0 between any two
    return [[1.0 if i == j else 0.0 for j in range(len(texts))] for i in range(len(texts))]


def test_cluster_collapses_near_duplicates():
    cands = [_c("a", "likes rust"), _c("b", "prefers rust"), _c("c", "likes go")]
    out = _run(cluster_candidates(cands, embed_fn=_identical_embed, similarity_threshold=0.7))
    # all collapse to one representative (identical embeddings)
    assert len(out) == 1
    assert out[0].metadata.get("cluster_size") == 3
    assert set(out[0].metadata.get("cluster_member_ids")) == {"a", "b", "c"}


def test_cluster_keeps_distinct():
    cands = [_c("a", "x"), _c("b", "y"), _c("c", "z")]
    out = _run(cluster_candidates(cands, embed_fn=_orthogonal_embed, similarity_threshold=0.7))
    assert len(out) == 3


def test_cluster_no_embed_is_noop():
    cands = [_c("a", "x"), _c("b", "y")]
    out = _run(cluster_candidates(cands, embed_fn=None))
    assert out == cands


def test_cluster_single_candidate_noop():
    cands = [_c("a", "x")]
    out = _run(cluster_candidates(cands, embed_fn=_identical_embed))
    assert out == cands


def test_cluster_embed_failure_degrades():
    async def _boom(texts):
        raise RuntimeError("embed down")

    cands = [_c("a", "x"), _c("b", "y")]
    out = _run(cluster_candidates(cands, embed_fn=_boom))
    assert out == cands  # falls back to no clustering, never raises


# -- semantic embed fallback ------------------------------------------------

def test_semantic_embed_falls_back_to_lexical_without_model(monkeypatch):
    # no embed_model configured -> uses lexical_embed (deterministic, offline)
    monkeypatch.setattr(llm, "_embed_model", lambda: "")
    vecs = _run(llm.semantic_embed(["rust is great", "rust is great"]))
    dot = sum(a * b for a, b in zip(vecs[0], vecs[1]))
    assert abs(dot - 1.0) < 1e-9  # identical texts -> cosine 1 (lexical)


def test_semantic_embed_falls_back_on_client_error(monkeypatch):
    monkeypatch.setattr(llm, "_embed_model", lambda: "text-embedding-3-small")
    # aux client import/use will fail in this env -> lexical fallback, no crash
    vecs = _run(llm.semantic_embed(["alpha", "beta"]))
    assert len(vecs) == 2 and len({len(v) for v in vecs}) == 1


# -- cron-miss catch-up (widens the fetch limit after a long gap) ------------

def test_catch_up_widens_fetch_limit(tmp_path, monkeypatch):
    """A gap > catch_up_factor * interval must widen the candidate fetch limit."""
    import time

    from plugins.dreaming import runner
    from plugins.dreaming.config import load_dreaming_config
    from plugins.dreaming.store import DreamStore

    captured = {}

    def fake_digests(db_path, *, since_ts=0.0, limit=50):
        captured["limit"] = limit
        return []  # no digests -> cycle ends early after stamping the run

    monkeypatch.setattr(runner.candmod, "build_session_digests", fake_digests)

    cfg = load_dreaming_config({"min_interval_hours": 6})
    store = DreamStore(tmp_path / "d.db")
    # last run was 100h ago >> 2 * 6h -> catch-up due
    store.set_last_run_ts(time.time() - 100 * 3600.0)

    db = tmp_path / "state.db"
    db.write_text("")  # exists; fake_digests ignores it
    _run(runner.run_dream_cycle(force=True, config=cfg, db_path=db, store=store))
    assert captured["limit"] == cfg.candidate_fetch_limit * 4
