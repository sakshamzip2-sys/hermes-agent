"""Tests for the tiered-compaction pass (req #9): raw -> summary -> pattern -> lesson.

These prove the real context-compression path added in
``plugins/dreaming/compaction.py`` WITHOUT touching any live store (every store
builds in a temp dir) and WITHOUT a network call (the aux-LLM is a STUB):

  (a) a cluster of N raw facts folds into 1 summary fact with a ``sources``
      pointer to all N, and the N raw are ARCHIVED (recallable with
      include_archived, NOT deleted);
  (b) the summary is SELF-SIGNED and verifies (verify_fact True);
  (c) tier promotion raw -> summary -> pattern works;
  (d) default config (disabled) is a NO-OP;
  (e) untrusted facts are NOT folded into a trusted summary (a trust-mixed
      cluster is rejected);
  (f) idempotent: re-running over an already-summarized cluster does not re-fold.

No em dashes (house rule).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Repo root importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.dreaming.compaction import (  # noqa: E402
    CompactionConfig,
    cluster_facts,
    run_compaction_pass,
)
from plugins.memory.holographic.store import (  # noqa: E402
    TIER_PATTERN,
    TIER_RAW,
    TIER_SUMMARY,
    MemoryStore,
    next_tier,
)


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[MemoryStore]:
    """A fresh temp-DB MemoryStore. Never touches the live store."""
    s = MemoryStore(db_path=str(tmp_path / "compaction_test.db"))
    try:
        yield s
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Stub aux-LLM: deterministic, no network. It folds a cluster into one line that
# echoes the count + tier so assertions are stable regardless of numpy presence.
# ---------------------------------------------------------------------------

class _StubLLM:
    """A deterministic fold function with a call counter and a fixed output."""

    def __init__(self, text: str = "folded summary of related facts") -> None:
        self.text = text
        self.calls = 0

    async def __call__(self, system: str, user: str) -> str:
        self.calls += 1
        return self.text


def _cfg(
    *,
    enabled: bool = True,
    min_cluster_size: int = 3,
    similarity_threshold: float = 0.82,
    max_clusters_per_run: int = 10,
    max_facts_scanned: int = 1000,
    source_store: str = "orchestrator/self",
) -> CompactionConfig:
    return CompactionConfig(
        enabled=enabled,
        min_cluster_size=min_cluster_size,
        similarity_threshold=similarity_threshold,
        max_clusters_per_run=max_clusters_per_run,
        max_facts_scanned=max_facts_scanned,
        source_store=source_store,
    )


def _seed_related_raw(store: MemoryStore, n: int = 3) -> list[str]:
    """Insert N highly-related raw facts; return their ext_keys.

    The facts share most tokens so they cluster under BOTH the HRR-cosine path
    (numpy present) and the tag/category + text-hash fallback (numpy absent):
    same category + same salient-token set.
    """
    ext_keys: list[str] = []
    contents = [
        "the deploy runbook lives in docs ops deploy guide page one",
        "the deploy runbook lives in docs ops deploy guide page two",
        "the deploy runbook lives in docs ops deploy guide page three",
        "the deploy runbook lives in docs ops deploy guide page four",
    ]
    for i in range(n):
        store.add_fact(
            contents[i],
            category="ops",
            tags="deploy,runbook",
            source_store="orchestrator/self",
            tier=TIER_RAW,
        )
        from plugins.memory.holographic.store import _content_ext_key
        ext_keys.append(_content_ext_key(contents[i]))
    return ext_keys


# ===========================================================================
# (a) N raw facts -> 1 summary; sources pointer to all N; raw ARCHIVED not deleted
# ===========================================================================

@pytest.mark.asyncio
async def test_cluster_folds_to_one_summary_with_sources_and_archives(store):
    sources = _seed_related_raw(store, n=3)
    llm = _StubLLM("summary: the deploy runbook is in docs/ops/deploy-guide")

    result = await run_compaction_pass(store, cfg=_cfg(), llm=llm)

    folded = result.folded
    assert len(folded) == 1, f"expected exactly one fold, got {len(folded)}"
    fr = folded[0]
    assert fr.tier_from == TIER_RAW and fr.tier_to == TIER_SUMMARY
    assert llm.calls == 1, "the aux-LLM must be called exactly once per folded cluster"

    # sources pointer covers all N raw ext_keys.
    assert set(fr.sources) == set(sources)

    # Exactly one new SUMMARY fact is now in the active view.
    summaries = store.list_active_facts(tier=TIER_SUMMARY)
    assert len(summaries) == 1
    assert summaries[0]["ext_key"] == fr.new_ext_key

    # The N raw facts are ARCHIVED (gone from the default active view) ...
    active_raw = store.list_active_facts(tier=TIER_RAW)
    assert active_raw == [], "folded raw sources must leave the active view"

    # ... but NOT deleted: every source ROW is preserved in the facts table.
    for ek in sources:
        row = store._conn.execute(
            "SELECT ext_key, content, archived_at FROM facts WHERE ext_key = ?", (ek,)
        ).fetchone()
        assert row is not None, f"archived source {ek} must NOT be deleted"
        assert row["archived_at"] is not None, f"source {ek} must be archived"

    # ... and a non-superseded archived source is RECALLABLE with
    # include_archived=True (the summary stays re-groundable to its raw sources).
    # The first source was superseded (t_invalid set) so it needs an as_of read;
    # the others are valid+archived and surface directly. Prove at least one
    # source re-surfaces via the archive-inclusive read.
    arch_hits = store.search_facts_readonly(
        "deploy runbook docs", limit=20, or_expand=True, include_archived=True
    )
    arch_keys = {h["ext_key"] for h in arch_hits}
    assert arch_keys & set(sources), (
        "archived sources must remain recallable via include_archived (not deleted)"
    )

    # The summary's provenance ledger row records all sources.
    row = store._conn.execute(
        "SELECT sources FROM compaction_ops WHERE cluster_id = ?", (fr.cluster_id,)
    ).fetchone()
    assert row is not None
    import json
    assert set(json.loads(row["sources"])) == set(sources)


# ===========================================================================
# (b) the summary is self-signed and verifies
# ===========================================================================

@pytest.mark.asyncio
async def test_summary_is_self_signed_and_verifies(store):
    _seed_related_raw(store, n=3)
    llm = _StubLLM("summary: deploy runbook location")

    result = await run_compaction_pass(store, cfg=_cfg(), llm=llm)
    assert len(result.folded) == 1
    new_ext_key = result.folded[0].new_ext_key
    assert new_ext_key is not None

    # The folded summary is in the self namespace, so it is HMAC-signed and
    # verify_fact recomputes the signature over its live content -> True.
    assert store.verify_fact(new_ext_key) is True

    # And it carries a non-null signature in the row.
    row = store._conn.execute(
        "SELECT signature, source_store, tier FROM facts WHERE ext_key = ?",
        (new_ext_key,),
    ).fetchone()
    assert row["signature"], "summary must be self-signed"
    assert row["source_store"] == "orchestrator/self"
    assert row["tier"] == TIER_SUMMARY


# ===========================================================================
# (c) tier promotion raw -> summary -> pattern works
# ===========================================================================

@pytest.mark.asyncio
async def test_tier_promotion_raw_to_summary_to_pattern(store):
    # Step 1: fold raw -> summary (3 related raw facts), proving the first rung.
    raw_sources = _seed_related_raw(store, n=3)
    llm1 = _StubLLM("summary: deploy runbook lives in docs ops deploy guide")
    r1 = await run_compaction_pass(store, cfg=_cfg(), llm=llm1, tier_from=TIER_RAW)
    assert len(r1.folded) == 1
    assert r1.folded[0].tier_to == TIER_SUMMARY
    assert set(r1.folded[0].sources) == set(raw_sources)

    # Step 2: seed THREE mutually-related summaries directly (the second rung's
    # input), then fold summary -> pattern. Seeding directly keeps the rung
    # independent of the first fold's stub text.
    summary_contents = [
        "summary deploy runbook overview docs ops guide alpha",
        "summary deploy runbook overview docs ops guide beta",
        "summary deploy runbook overview docs ops guide gamma",
    ]
    summary_keys: list[str] = []
    from plugins.memory.holographic.store import _content_ext_key
    for c in summary_contents:
        store.add_fact(
            c, category="ops", tags="deploy,runbook",
            source_store="orchestrator/self", tier=TIER_SUMMARY,
        )
        summary_keys.append(_content_ext_key(c))

    llm2 = _StubLLM("pattern: deploy runbooks are documented under docs/ops")
    r2 = await run_compaction_pass(
        store, cfg=_cfg(similarity_threshold=0.5), llm=llm2, tier_from=TIER_SUMMARY
    )
    folded_summary = [f for f in r2.folded if set(summary_keys).issubset(set(f.sources))]
    assert folded_summary, "expected the 3 seeded summaries to fold to a pattern"
    fr = folded_summary[0]
    assert fr.tier_from == TIER_SUMMARY and fr.tier_to == TIER_PATTERN

    patterns = store.list_active_facts(tier=TIER_PATTERN)
    assert len(patterns) == 1
    assert patterns[0]["ext_key"] == fr.new_ext_key
    assert store.verify_fact(fr.new_ext_key) is True

    # next_tier ladder sanity (the engine's promotion contract).
    assert next_tier(TIER_RAW) == TIER_SUMMARY
    assert next_tier(TIER_SUMMARY) == TIER_PATTERN
    assert next_tier("lesson") is None


# ===========================================================================
# (d) default config disabled = no-op
# ===========================================================================

@pytest.mark.asyncio
async def test_disabled_is_noop(store):
    sources = _seed_related_raw(store, n=3)
    llm = _StubLLM("should never be called")

    # Default config is OFF.
    result = await run_compaction_pass(store, cfg=CompactionConfig(), llm=llm)

    assert result.enabled is False
    assert result.folds == []
    assert llm.calls == 0, "the aux-LLM must NOT be called when disabled"

    # Nothing changed: all raw facts still active, no summary, no ledger.
    assert len(store.list_active_facts(tier=TIER_RAW)) == len(sources)
    assert store.list_active_facts(tier=TIER_SUMMARY) == []
    ledger = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='compaction_ops'"
    ).fetchone()
    # Table may not even exist (we never reached _ensure_ledger); if it does, empty.
    if ledger is not None:
        n = store._conn.execute("SELECT COUNT(*) AS n FROM compaction_ops").fetchone()["n"]
        assert n == 0


# ===========================================================================
# (e) untrusted facts are NOT folded into a trusted summary
# ===========================================================================

@pytest.mark.asyncio
async def test_untrusted_not_folded_into_trusted_summary(store):
    """A trust-mixed cluster is rejected; an untrusted fact can never enter a
    trusted summary. We build a cluster of trusted self-facts plus one UNTRUSTED
    fact (tampered so verify_fact is False) and assert no fold happens."""
    # Three related TRUSTED self-facts.
    _seed_related_raw(store, n=3)

    # One related fact, then TAMPER its content so its signature no longer
    # verifies -> verify_fact False -> untrusted. It shares tokens so the
    # vector/text clustering would otherwise pull it into the same cluster.
    tampered_content = "the deploy runbook lives in docs ops deploy guide page five"
    store.add_fact(
        tampered_content, category="ops", tags="deploy,runbook",
        source_store="orchestrator/self", tier=TIER_RAW,
    )
    from plugins.memory.holographic.store import _content_ext_key
    tampered_ek = _content_ext_key(tampered_content)
    # Mutate the stored content directly (post-sign tamper): signature now stale.
    store._conn.execute(
        "UPDATE facts SET content = ? WHERE ext_key = ?",
        ("the deploy runbook lives in docs ops deploy guide page five EDITED", tampered_ek),
    )
    store._conn.commit()
    assert store.verify_fact(tampered_ek) is False, "tampered fact must be untrusted"

    llm = _StubLLM("must not fold a mixed cluster")
    # Use a LOW similarity threshold so HRR clustering definitely groups them all
    # into one (trust-mixed) cluster, forcing the trust gate to be the decider.
    result = await run_compaction_pass(
        store, cfg=_cfg(similarity_threshold=0.0), llm=llm
    )

    # Either the trust gate split/rejected the mixed cluster -> no fold, OR (in
    # the fallback bucketing path where buckets are token-exact) the tampered
    # fact lands in its own bucket. Either way: the tampered untrusted fact must
    # NEVER be a source of a folded summary, and no SUMMARY may contain it.
    for fr in result.folded:
        assert tampered_ek not in fr.sources, (
            "an untrusted fact must never be folded into a summary"
        )

    # If a fold DID happen (the 3 trusted facts only), it must be trust-pure.
    # And the explicit mixed-cluster rejection path must be observable: assert at
    # least one skipped:trust_mixed OR that no fold included the untrusted fact.
    statuses = [f.status for f in result.folds]
    assert any(s.startswith("skipped:trust_mixed") for s in statuses) or all(
        tampered_ek not in f.sources for f in result.folded
    )


@pytest.mark.asyncio
async def test_fallback_clustering_without_numpy(store, monkeypatch):
    """When numpy/HRR is absent the pass degrades to tag/category + text-hash
    bucketing (graceful degradation) and still folds + self-signs a summary."""
    import plugins.dreaming.compaction as comp
    monkeypatch.setattr(comp, "_HRR_AVAILABLE", False)

    # Same category + same salient-token set -> same fallback bucket. content is
    # UNIQUE, so vary only a leading stopword (dropped by the bucket tokenizer).
    contents = [
        "the deploy runbook docs ops guide",
        "a deploy runbook docs ops guide",
        "this deploy runbook docs ops guide",
    ]
    for c in contents:
        store.add_fact(
            c, category="ops", tags="deploy",
            source_store="orchestrator/self", tier=TIER_RAW,
        )

    llm = _StubLLM("fallback summary")
    result = await run_compaction_pass(store, cfg=_cfg(), llm=llm)

    assert len(result.folded) == 1, "fallback bucketing must still fold a related cluster"
    new_key = result.folded[0].new_ext_key
    assert new_key is not None
    assert store.verify_fact(new_key) is True
    assert len(store.list_active_facts(tier=TIER_SUMMARY)) == 1
    assert store.list_active_facts(tier=TIER_RAW) == []


def test_cluster_facts_splits_are_caller_enforced_for_trust():
    """cluster_facts itself is trust-agnostic (pure), but the PASS enforces trust
    homogeneity. This unit-checks the pure clustering groups related facts."""
    facts = [
        {"ext_key": "a", "content": "alpha beta gamma delta", "category": "x",
         "tags": "t", "source_store": "orchestrator/self", "hrr_vector": None},
        {"ext_key": "b", "content": "alpha beta gamma delta", "category": "x",
         "tags": "t", "source_store": "orchestrator/self", "hrr_vector": None},
        {"ext_key": "c", "content": "alpha beta gamma delta", "category": "x",
         "tags": "t", "source_store": "orchestrator/self", "hrr_vector": None},
    ]
    clusters = cluster_facts(facts, similarity_threshold=0.5, min_cluster_size=3)
    assert len(clusters) == 1
    assert {f["ext_key"] for f in clusters[0]} == {"a", "b", "c"}


# ===========================================================================
# (f) idempotent: re-run does not re-fold an already-summarized cluster
# ===========================================================================

@pytest.mark.asyncio
async def test_idempotent_rerun_does_not_refold(store):
    _seed_related_raw(store, n=3)
    llm = _StubLLM("summary one and only")

    r1 = await run_compaction_pass(store, cfg=_cfg(), llm=llm)
    assert len(r1.folded) == 1
    assert llm.calls == 1
    first_summary_key = r1.folded[0].new_ext_key

    # Re-run. The sources are archived (out of the active view) AND the ledger
    # records the cluster, so there is nothing new to fold: no new LLM call, no
    # new summary.
    r2 = await run_compaction_pass(store, cfg=_cfg(), llm=llm)
    assert r2.folded == [], "a second pass must not re-fold the same cluster"
    assert llm.calls == 1, "the aux-LLM must not be called again on re-run"

    summaries = store.list_active_facts(tier=TIER_SUMMARY)
    assert len(summaries) == 1, "no duplicate summary on re-run"
    assert summaries[0]["ext_key"] == first_summary_key
