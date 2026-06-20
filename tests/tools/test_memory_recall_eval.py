"""Regression gate for the memory retrieval-quality eval (requirement #7).

This is the gate every later memory-layer change must clear: it runs the frozen
gold set (skills/memory-eval/gold/memory_gold.yaml) through the eval harness
against today's holographic FTS5 baseline and asserts:

  1. the aggregate OR-expanded recall@5 stays at or above a FROZEN floor
     (RECALL_FLOOR = 0.8, matching memory-stack/recall_probe.RECALL_FLOOR);
  2. abstention queries (empty relevant set) return nothing - the store must
     not surface noise labelled authoritative;
  3. the NL-vs-OR gap the design relies on is real (raw NL recall lags OR);
  4. the metric primitives (recall / precision / MRR / nDCG) are correct,
     pinned against hand-computed expected values.

It mutates NO real memory (the harness builds a temp MemoryStore in a temp dir),
so it is safe to re-run after a session restart (requirement #5). The eval module
lives in a hyphenated skill directory, so it is loaded from its file path.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import pytest

# Frozen floor: at least 80% of relevant facts must be retrievable in the top-5
# under OR-expansion. Matches memory-stack/recall_probe.RECALL_FLOOR. Do not
# lower this; raise it only when a real retrieval improvement makes it stick.
RECALL_FLOOR = 0.8

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EVAL_PATH = _REPO_ROOT / "skills" / "memory-eval" / "eval.py"
_GOLD_PATH = _REPO_ROOT / "skills" / "memory-eval" / "gold" / "memory_gold.yaml"


def _load_eval_module():
    """Import skills/memory-eval/eval.py by file path (hyphenated dir is not a package)."""
    spec = importlib.util.spec_from_file_location("memory_eval_harness", _EVAL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def evalmod():
    return _load_eval_module()


@pytest.fixture(scope="module")
def report(evalmod):
    assert _GOLD_PATH.exists(), f"gold set missing at {_GOLD_PATH}"
    return evalmod.run_eval(str(_GOLD_PATH))


# ----------------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------------

def test_or_expanded_recall_at_5_clears_floor(report):
    """The regression gate: OR-expanded aggregate recall@5 >= the frozen floor."""
    recall5 = report["modes"]["or"]["aggregate"]["recall@5"]
    assert recall5 >= RECALL_FLOOR, (
        f"OR-expanded recall@5 {recall5:.3f} dropped below frozen floor {RECALL_FLOOR}; "
        "a memory-layer change regressed retrieval quality"
    )


def test_abstention_queries_return_nothing(report):
    """Every abstention query (empty relevant set) must return an empty result.

    Checked in BOTH retrieval modes: injecting low-relevance candidates as
    authoritative reference data is worse than returning nothing.
    """
    for mode in ("nl", "or"):
        for pq in report["modes"][mode]["per_query"]:
            if pq["metrics"].get("abstention"):
                assert pq["metrics"]["abstained"], (
                    f"abstention query {pq['id']!r} returned "
                    f"{pq['metrics']['num_returned']} rows in mode={mode} "
                    f"(expanded={pq['expanded_query']!r}); it must abstain"
                )
                # And abstention is scored as a pass (1.0) when it abstains.
                assert pq["metrics"]["recall@5"] == 1.0


def test_nl_vs_or_gap_is_real(report):
    """Raw NL recall@5 must lag OR-expanded recall@5 (the gap the merge layer exploits).

    If this ever inverts, either FTS5 stopped implicit-ANDing or the gold set
    lost its NL-filler queries - both invalidate the OR-expansion rationale.
    """
    nl = report["modes"]["nl"]["aggregate"]["recall@5"]
    orr = report["modes"]["or"]["aggregate"]["recall@5"]
    assert nl < orr, (
        f"expected raw NL recall@5 ({nl:.3f}) to lag OR-expanded ({orr:.3f}); "
        "the NL-vs-OR gap that motivates OR-expansion is gone"
    )


def test_gold_set_is_well_formed(evalmod):
    """The gold set loads, has the five query types, and every relevance id resolves."""
    corpus, queries = evalmod.load_gold(str(_GOLD_PATH))
    assert len(corpus) >= 30, "gold corpus should have ~35 facts"
    assert len(queries) >= 18, "gold should have ~20 queries"
    types = {q.get("type") for q in queries}
    assert {"single_hop", "multi_hop", "temporal", "knowledge_update", "abstention"} <= types, (
        f"gold set is missing query types; found {types}"
    )
    corpus_ids = {f["id"] for f in corpus}
    for q in queries:
        for fid in (q.get("relevant_fact_ids") or []):
            assert fid in corpus_ids, f"query {q['id']} references unknown id {fid}"


def test_no_secrets_in_gold_set():
    """The gold set must carry no secrets / API keys / obvious PII (requirement #8)."""
    import re
    text = _GOLD_PATH.read_text(encoding="utf-8")
    forbidden = [
        re.compile(r"AKIA[0-9A-Z]{16}"),                 # AWS access key id
        re.compile(r"sk-[A-Za-z0-9]{20,}"),              # OpenAI-style secret key
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # private key block
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),            # US SSN
        re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"),  # card number
    ]
    for pat in forbidden:
        assert not pat.search(text), f"gold set contains a secret/PII pattern: {pat.pattern}"


# ----------------------------------------------------------------------------
# Metric-correctness pins (so a refactor of the formulas is caught immediately)
# ----------------------------------------------------------------------------

def test_metric_primitives_are_correct(evalmod):
    """Hand-computed expected values for each BEIR-style metric."""
    # ranking: relevant items are 'a' (rank 2) and 'd' (rank 4) of 5.
    ranked = ["x", "a", "y", "d", "z"]
    relevant = {"a", "d"}

    # recall@k = (relevant found in top-k) / |relevant|
    assert evalmod.recall_at_k(ranked, relevant, 1) == 0.0          # top-1 = x
    assert evalmod.recall_at_k(ranked, relevant, 2) == 0.5          # a found
    assert evalmod.recall_at_k(ranked, relevant, 4) == 1.0          # a + d
    assert evalmod.recall_at_k(ranked, relevant, 5) == 1.0

    # precision@k = (relevant in top-k) / k   (no allowed-only filtering here)
    assert evalmod.precision_at_k(ranked, relevant, set(), 1) == 0.0
    assert evalmod.precision_at_k(ranked, relevant, set(), 2) == pytest.approx(0.5)
    assert evalmod.precision_at_k(ranked, relevant, set(), 4) == pytest.approx(0.5)

    # hit_rate@k = 1 if any relevant in top-k
    assert evalmod.hit_rate_at_k(ranked, relevant, 1) == 0.0
    assert evalmod.hit_rate_at_k(ranked, relevant, 2) == 1.0

    # MRR = 1 / rank of first relevant = 1/2
    assert evalmod.mrr(ranked, relevant) == pytest.approx(0.5)

    # nDCG@10: DCG = 1/log2(3) + 1/log2(5); IDCG = 1/log2(2) + 1/log2(3)
    dcg = 1.0 / math.log2(3) + 1.0 / math.log2(5)
    idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
    assert evalmod.ndcg_at_k(ranked, relevant, 10) == pytest.approx(dcg / idcg)


def test_precision_excludes_allowed_only_from_denominator(evalmod):
    """An allowed-but-not-required fact (e.g. a superseded knowledge-update fact)
    at rank 1 is neither a hit nor a penalty: it is excluded from precision's
    denominator, so a single allowed-only top result yields 0/0 -> 0.0, and a
    [allowed_old, relevant_new] ranking yields precision@2 = 1/1 = 1.0."""
    # rank1 = superseded 'old' (allowed, not relevant), rank2 = current 'new' (relevant)
    ranked = ["old", "new"]
    relevant = {"new"}
    allowed = {"old"}
    # @1: only slot is allowed-only -> excluded -> empty denominator -> 0.0
    assert evalmod.precision_at_k(ranked, relevant, allowed, 1) == 0.0
    # @2: 'old' excluded, 'new' counts -> 1 hit / 1 scored = 1.0
    assert evalmod.precision_at_k(ranked, relevant, allowed, 2) == pytest.approx(1.0)


def test_perfect_and_empty_rankings(evalmod):
    """Edge cases: a perfect ranking scores 1.0 across the board; an empty
    ranking against a non-empty relevant set scores 0.0 (a real miss)."""
    relevant = {"a", "b"}
    perfect = ["a", "b", "c"]
    assert evalmod.recall_at_k(perfect, relevant, 2) == 1.0
    assert evalmod.mrr(perfect, relevant) == 1.0
    assert evalmod.ndcg_at_k(perfect, relevant, 10) == pytest.approx(1.0)

    empty: list[str] = []
    assert evalmod.recall_at_k(empty, relevant, 5) == 0.0
    assert evalmod.mrr(empty, relevant) == 0.0
    assert evalmod.ndcg_at_k(empty, relevant, 10) == 0.0


def test_score_query_rewards_abstention(evalmod):
    """An abstention query (empty relevant set) scores 1.0 when nothing is
    returned and 0.0 when noise is returned."""
    abstained = evalmod.score_query([], set(), set())
    assert abstained["abstention"] is True
    assert abstained["abstained"] is True
    assert abstained["recall@5"] == 1.0
    assert abstained["mrr"] == 1.0

    leaked = evalmod.score_query(["noise1", "noise2"], set(), set())
    assert leaked["abstention"] is True
    assert leaked["abstained"] is False
    assert leaked["recall@5"] == 0.0
    assert leaked["mrr"] == 0.0
