#!/usr/bin/env python3
"""Memory retrieval-quality eval harness + CI gate (requirement #7).

`agent-eval` scores the agent's BEHAVIOR (which tool it picks, what it leaks).
This harness scores the memory layer's RETRIEVAL QUALITY: given a frozen gold
set of facts and queries, does the right fact actually surface? "Stored it and
got something back" is NOT success; the metric is recall@k / precision@k / MRR /
nDCG, BEIR-style, against a per-query relevant set.

It drives the real holographic store directly: it builds a TEMP MemoryStore
(no `~/.hermes` mutation, requirement #2), inserts every corpus fact via
`add_fact`, then for each query computes, against the query's
`relevant_fact_ids`:

    Recall@{1,3,5,10}, Precision@{1,3,5}, Hit-Rate@k, MRR, nDCG@10

It runs each query TWICE: once as the raw natural-language string, and once
OR-expanded (split, drop stopwords, join with ' OR '). FTS5 implicitly ANDs
query terms, so NL filler words force misses; the OR pass measures that lift
explicitly (the exact gap `memory-stack/recall_probe.py` proved: ~0.62 NL vs
~1.00 OR). This measures TODAY's FTS5 baseline; there is no merge layer yet.

Abstention queries (empty `relevant_fact_ids`) are scored by REWARDING an empty
result: an abstention query "passes" (recall=precision=1.0) only when the store
returns nothing, and scores 0.0 when it surfaces noise labelled authoritative.

CI gate: `--threshold <floor>` exits non-zero when the aggregate OR-expanded
recall@5 falls below the floor (mirrors `agent-eval`'s gate and
`recall_probe.RECALL_FLOOR`). `--mode {nl,or}` selects which retrieval pass the
gate scores (default: or).

Pure stdlib + the repo (PyYAML is already a repo dep). No new dependencies.

Usage:
    python eval.py --gold gold/memory_gold.yaml --threshold 0.8
    python eval.py --gold gold/memory_gold.yaml --json
    python eval.py --gold gold/memory_gold.yaml --mode nl --threshold 0.6
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Make the repo root importable so `plugins.memory.holographic.store` resolves
# whether the harness is run from the skill dir or the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Recall@k cutoffs reported. recall@5 is the gated metric (matches recall_probe).
RECALL_KS = (1, 3, 5, 10)
PRECISION_KS = (1, 3, 5)
HITRATE_KS = (1, 3, 5, 10)
NDCG_K = 10
# How many candidates to pull from the store per query. Must be >= max(RECALL_KS)
# so a relevant fact ranked at, say, 10 is not truncated before recall@10 sees it.
RETRIEVAL_LIMIT = 20

# FTS5 implicitly ANDs query terms, so natural-language filler words ("what is
# my ...") force a miss when the stored fact lacks them. The OR-expansion strips
# these and ORs the rest. Kept in sync with memory-stack/recall_probe.py.
_STOPWORDS = {
    "what", "is", "my", "do", "i", "the", "a", "an", "in", "of", "to",
    "where", "which", "are", "you", "does", "how", "me", "on", "for",
    "name", "when", "use", "uses", "used", "now", "current", "currently",
    "did", "was", "were", "have", "has", "and", "or", "with", "that",
}


def or_expand(query: str) -> str:
    """Split, drop stopwords, join survivors with ' OR ' for FTS5.

    Falls back to the raw query if every token is a stopword (so a degenerate
    all-stopword query still searches for something rather than nothing).
    """
    terms = [_clean_token(t) for t in query.lower().split()]
    terms = [t for t in terms if t and t not in _STOPWORDS]
    return " OR ".join(terms) if terms else query


def _clean_token(tok: str) -> str:
    """Strip FTS5-significant punctuation so a bare term cannot become a syntax
    error or an unintended column/operator token."""
    return "".join(ch for ch in tok if ch.isalnum())


# ----------------------------------------------------------------------------
# Metric primitives (BEIR-style). Verified against the standard definitions.
# ----------------------------------------------------------------------------

def recall_at_k(ranked_ids: Sequence[str], relevant: set, k: int) -> float:
    """|relevant retrieved in top-k| / |relevant|.

    Undefined when there are no relevant items; abstention is handled separately
    by `score_query`, so this is only called with a non-empty relevant set.
    """
    if not relevant:
        return 0.0
    topk = ranked_ids[:k]
    hits = sum(1 for fid in relevant if fid in topk)
    return hits / len(relevant)


def precision_at_k(ranked_ids: Sequence[str], relevant: set,
                   allowed: set, k: int) -> float:
    """|relevant retrieved in top-k| / k.

    `allowed` (e.g. a superseded knowledge-update fact) is neither a hit nor a
    penalty: an allowed-but-not-required result is excluded from the denominator
    so it does not depress precision. The denominator is the count of top-k slots
    that are NOT allowed-only (i.e. real judgement opportunities)."""
    topk = ranked_ids[:k]
    if not topk:
        return 0.0
    scored = [fid for fid in topk if fid not in allowed or fid in relevant]
    if not scored:
        return 0.0
    hits = sum(1 for fid in scored if fid in relevant)
    return hits / len(scored)


def hit_rate_at_k(ranked_ids: Sequence[str], relevant: set, k: int) -> float:
    """1.0 if ANY relevant item is in the top-k, else 0.0 (a.k.a. success@k)."""
    if not relevant:
        return 0.0
    topk = set(ranked_ids[:k])
    return 1.0 if (topk & relevant) else 0.0


def mrr(ranked_ids: Sequence[str], relevant: set) -> float:
    """Reciprocal rank of the FIRST relevant item (1-indexed); 0 if none found."""
    if not relevant:
        return 0.0
    for idx, fid in enumerate(ranked_ids, start=1):
        if fid in relevant:
            return 1.0 / idx
    return 0.0


def ndcg_at_k(ranked_ids: Sequence[str], relevant: set, k: int) -> float:
    """Binary-relevance nDCG@k.

    DCG = sum over ranks i (1-indexed) of rel_i / log2(i + 1), with rel_i in
    {0, 1}. IDCG is the DCG of the ideal ranking (all relevant items first),
    capped at min(|relevant|, k) ones. nDCG = DCG / IDCG, in [0, 1].
    """
    if not relevant:
        return 0.0
    dcg = 0.0
    for i, fid in enumerate(ranked_ids[:k], start=1):
        if fid in relevant:
            dcg += 1.0 / math.log2(i + 1)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return (dcg / idcg) if idcg > 0 else 0.0


# ----------------------------------------------------------------------------
# Per-query scoring
# ----------------------------------------------------------------------------

def score_query(ranked_ids: Sequence[str], relevant: set, allowed: set) -> Dict[str, Any]:
    """Compute the full metric bundle for one query's ranked result list.

    Abstention (empty relevant set): the correct behavior is to return NOTHING.
    We reward that by reporting 1.0 on every metric when the result is empty, and
    0.0 when noise was returned. `abstained` records whether the store actually
    abstained, so the regression test can assert it directly.
    """
    is_abstention = not relevant
    if is_abstention:
        abstained = len(ranked_ids) == 0
        val = 1.0 if abstained else 0.0
        out: Dict[str, Any] = {
            "abstention": True,
            "abstained": abstained,
            "num_returned": len(ranked_ids),
        }
        for k in RECALL_KS:
            out[f"recall@{k}"] = val
        for k in PRECISION_KS:
            out[f"precision@{k}"] = val
        for k in HITRATE_KS:
            out[f"hit_rate@{k}"] = val
        out["mrr"] = val
        out[f"ndcg@{NDCG_K}"] = val
        return out

    out = {
        "abstention": False,
        "abstained": len(ranked_ids) == 0,
        "num_returned": len(ranked_ids),
    }
    for k in RECALL_KS:
        out[f"recall@{k}"] = recall_at_k(ranked_ids, relevant, k)
    for k in PRECISION_KS:
        out[f"precision@{k}"] = precision_at_k(ranked_ids, relevant, allowed, k)
    for k in HITRATE_KS:
        out[f"hit_rate@{k}"] = hit_rate_at_k(ranked_ids, relevant, k)
    out["mrr"] = mrr(ranked_ids, relevant)
    out[f"ndcg@{NDCG_K}"] = ndcg_at_k(ranked_ids, relevant, NDCG_K)
    return out


# ----------------------------------------------------------------------------
# Gold-set loading / validation
# ----------------------------------------------------------------------------

def load_gold(path: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    import yaml
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("gold file must be a mapping with 'corpus' and 'queries'")
    corpus = data.get("corpus") or []
    queries = data.get("queries") or []
    if not isinstance(corpus, list) or not corpus:
        raise ValueError("gold 'corpus' must be a non-empty list")
    if not isinstance(queries, list) or not queries:
        raise ValueError("gold 'queries' must be a non-empty list")

    corpus_ids = set()
    for fact in corpus:
        fid = fact.get("id")
        if not fid:
            raise ValueError(f"corpus fact missing 'id': {fact!r}")
        if fid in corpus_ids:
            raise ValueError(f"duplicate corpus id: {fid}")
        if not str(fact.get("content", "")).strip():
            raise ValueError(f"corpus fact {fid} has empty content")
        corpus_ids.add(fid)

    # Every referenced relevant/allowed id must exist in the corpus, otherwise a
    # query can never score a hit and the gate is silently un-clearable.
    for q in queries:
        if not q.get("id"):
            raise ValueError(f"query missing 'id': {q!r}")
        for key in ("relevant_fact_ids", "allowed_fact_ids"):
            for fid in (q.get(key) or []):
                if fid not in corpus_ids:
                    raise ValueError(
                        f"query {q['id']} references unknown corpus id {fid!r} in {key}"
                    )
    return corpus, queries


# ----------------------------------------------------------------------------
# Retrieval against a fresh temp MemoryStore
# ----------------------------------------------------------------------------

def build_store(corpus: List[Dict[str, Any]], db_path: Path):
    """Insert every corpus fact into a fresh MemoryStore. Returns (store, content->id map).

    The store's `add_fact` dedups by content and returns its OWN autoincrement
    fact_id; we keep a content->gold_id map so retrieved rows map back to the
    stable gold id the relevance judgements use.
    """
    from plugins.memory.holographic.store import MemoryStore
    store = MemoryStore(db_path=db_path)
    content_to_gold: Dict[str, str] = {}
    for fact in corpus:
        content = str(fact["content"]).strip()
        store.add_fact(
            content,
            category=str(fact.get("category", "general")),
            tags=str(fact.get("tags", "")),
        )
        content_to_gold[content] = fact["id"]
    return store, content_to_gold


def retrieve(store, content_to_gold: Dict[str, str], query: str,
             limit: int = RETRIEVAL_LIMIT) -> List[str]:
    """Run one FTS5 search and map each result row back to its gold id.

    Uses `search_facts_readonly` (the actual recall-path variant: no write on
    read, separate ro WAL connection) with `min_trust=0.0` so default-trust
    facts (0.5) are never floored out, and maps the result content back to the
    stable gold id. The caller already applies NL->OR expansion per mode, so
    `or_expand` stays False here to avoid double-expanding. Rows whose content
    is not in the map (should not happen for the gold corpus) are dropped rather
    than guessed.
    """
    q = query.strip()
    if not q:
        return []
    rows = store.search_facts_readonly(q, min_trust=0.0, limit=limit, or_expand=False)
    ranked: List[str] = []
    for r in rows:
        gold_id = content_to_gold.get(str(r.get("content", "")).strip())
        if gold_id is not None:
            ranked.append(gold_id)
    return ranked


# ----------------------------------------------------------------------------
# Aggregation
# ----------------------------------------------------------------------------

_METRIC_KEYS = (
    [f"recall@{k}" for k in RECALL_KS]
    + [f"precision@{k}" for k in PRECISION_KS]
    + [f"hit_rate@{k}" for k in HITRATE_KS]
    + ["mrr", f"ndcg@{NDCG_K}"]
)


def _aggregate(per_query: List[Dict[str, Any]]) -> Dict[str, float]:
    """Macro-average each metric across all queries (abstention included)."""
    if not per_query:
        return {key: 0.0 for key in _METRIC_KEYS}
    agg: Dict[str, float] = {}
    n = len(per_query)
    for key in _METRIC_KEYS:
        agg[key] = round(sum(q["metrics"][key] for q in per_query) / n, 4)
    return agg


# ----------------------------------------------------------------------------
# Merged mode: MergeLayer.recall over BOTH local planes (session FTS5 + holographic)
#
# Proves the merge layer fuses TWO planes, not just FTS5. We seed the SAME gold
# corpus into a temp holographic store AND a temp SessionDB (one user message per
# fact), then run agent.memory_merge.MergeLayer.recall over both adapters and map
# the fused candidates back to stable gold ids for the BEIR metrics. This is the
# req-#7 cross-store fused recall@5 the working slice must clear.
# ----------------------------------------------------------------------------

def build_session_store(corpus: List[Dict[str, Any]], db_path: Path):
    """Seed a temp SessionDB with one user message per corpus fact.

    Returns ``(db, message_id -> gold_id map)``. The session FTS5 plane indexes
    the message content, so the merge layer's SessionFTS5Adapter can retrieve
    the same facts as messages. We key the result map on the message id (which
    the adapter surfaces as the candidate id) so fused session hits map back to
    the stable gold id without relying on snippet text.
    """
    from hermes_state import SessionDB

    db = SessionDB(db_path=db_path)
    session_id = "memeval_seed"
    db.create_session(session_id, source="api_server")
    msgid_to_gold: Dict[int, str] = {}
    for fact in corpus:
        content = str(fact["content"]).strip()
        mid = db.append_message(session_id, role="user", content=content)
        msgid_to_gold[int(mid)] = fact["id"]
    return db, msgid_to_gold


def retrieve_merged(
    merge_layer,
    adapters,
    query: str,
    holo_content_to_gold: Dict[str, str],
    session_msgid_to_gold: Dict[int, str],
) -> Tuple[List[str], Dict[str, Any]]:
    """Run MergeLayer.recall over both planes; map fused candidates to gold ids.

    Returns ``(ranked_gold_ids, trace)``. A holographic candidate maps via its
    content; a session candidate maps via its message id. Candidates that do not
    resolve to a gold id (should not happen for the seeded corpus) are dropped
    rather than guessed. Duplicate gold ids (same fact surfacing from both
    planes) are collapsed first-seen so recall is not double-credited.
    """
    q = query.strip()
    if not q:
        return [], {}
    ranked_cands, trace = merge_layer.recall(q, stores=adapters)
    ranked: List[str] = []
    seen: set = set()
    for cand in ranked_cands:
        gold_id: Optional[str] = None
        if cand.source_store == "holographic":
            gold_id = holo_content_to_gold.get(str(cand.text_for_rerank).strip())
        elif cand.source_store == "session":
            try:
                gold_id = session_msgid_to_gold.get(int(cand.id))
            except (TypeError, ValueError):
                gold_id = None
        if gold_id is not None and gold_id not in seen:
            seen.add(gold_id)
            ranked.append(gold_id)
    return ranked, trace


def run_merged_eval(gold_path: str) -> Dict[str, Any]:
    """Score the gold set with the MergeLayer fused over {session, holographic}.

    Builds a temp holographic store and a temp SessionDB from the same corpus,
    fans out through the two local adapters, and reports the BEIR metric bundle
    on the fused result (cross-store fused recall@5 / precision@k / mrr / ndcg).
    """
    corpus, queries = load_gold(gold_path)

    from agent.memory_merge import (
        HolographicAdapter,
        MergeLayer,
        SessionFTS5Adapter,
    )

    with tempfile.TemporaryDirectory() as tmp:
        holo_store, holo_content_to_gold = build_store(
            corpus, Path(tmp) / "memory_eval_holo.db"
        )
        session_db, session_msgid_to_gold = build_session_store(
            corpus, Path(tmp) / "state_eval.db"
        )
        try:
            merge_layer = MergeLayer()
            adapters = [
                SessionFTS5Adapter(session_db, role_filter=["user"]),
                HolographicAdapter(holo_store),
            ]
            per_query: List[Dict[str, Any]] = []
            for q in queries:
                relevant = set(q.get("relevant_fact_ids") or [])
                allowed = set(q.get("allowed_fact_ids") or [])
                ranked, trace = retrieve_merged(
                    merge_layer, adapters, q["query"],
                    holo_content_to_gold, session_msgid_to_gold,
                )
                metrics = score_query(ranked, relevant, allowed)
                per_query.append({
                    "id": q["id"],
                    "type": q.get("type", "unknown"),
                    "query": q["query"],
                    "expanded_query": trace.get("expanded_query", ""),
                    "relevant": sorted(relevant),
                    "ranked": ranked[:NDCG_K],
                    "planes_queried": trace.get("planes_queried", []),
                    "planes_blocked": trace.get("planes_blocked", []),
                    "abstained": trace.get("abstained", False),
                    "metrics": metrics,
                })
        finally:
            holo_store.close()
            session_db.close()

    return {
        "gold": gold_path,
        "num_corpus": len(corpus),
        "num_queries": len(queries),
        "by_type": _per_type_counts(queries),
        "planes": ["session", "holographic"],
        "modes": {
            "merged": {
                "per_query": per_query,
                "aggregate": _aggregate(per_query),
            },
        },
    }


def _print_merged_report(report: Dict[str, Any], threshold: Optional[float],
                         gate_passed: Optional[bool]) -> None:
    print("=" * 76)
    print("Memory retrieval-quality eval (MergeLayer fused over session + holographic)")
    print("=" * 76)
    print(f"gold: {report['gold']}")
    print(f"corpus facts: {report['num_corpus']}   queries: {report['num_queries']}")
    print(f"planes fused: {', '.join(report['planes'])}")
    print(f"query types: " + ", ".join(f"{t}={n}" for t, n in sorted(report['by_type'].items())))
    print("-" * 76)
    agg = report["modes"]["merged"]["aggregate"]
    headline = ["recall@1", "recall@3", "recall@5", "recall@10",
                "precision@1", "precision@5", "mrr", f"ndcg@{NDCG_K}"]
    print(f"{'metric':<14}{'fused':>14}")
    for key in headline:
        print(f"{key:<14}{agg[key]:>14.3f}")
    print("-" * 76)
    print("per-query (mode=merged):")
    for pq in report["modes"]["merged"]["per_query"]:
        m = pq["metrics"]
        planes = "+".join(pq.get("planes_queried", []))
        if m.get("abstention"):
            mark = "OK " if m["abstained"] else "NOISE"
            print(f"  [{mark:>5}] {pq['type']:<16} {pq['query']!r} -> abstain "
                  f"(returned {m['num_returned']}, planes={planes})")
        else:
            r5 = m["recall@5"]
            mark = "HIT " if m["hit_rate@5"] else "MISS"
            print(f"  [{mark:>5}] {pq['type']:<16} {pq['query']!r} -> "
                  f"recall@5={r5:.2f} mrr={m['mrr']:.2f} planes={planes} ranked={pq['ranked'][:3]}")
    print("-" * 76)
    print(f"finding: cross-store FUSED recall@5={agg['recall@5']:.3f} over "
          "{session, holographic} (proves the merge fuses two planes, not just FTS5).")
    if threshold is not None:
        verdict = "GATE PASSED" if gate_passed else "GATE FAILED"
        print(f"gate: merged recall@5 {agg['recall@5']:.3f} "
              f"{'>=' if gate_passed else '<'} threshold {threshold:.3f} -> {verdict}")
    print("=" * 76)


def run_eval(gold_path: str) -> Dict[str, Any]:
    """Score the gold set in BOTH retrieval modes (nl, or). Returns a full report."""
    corpus, queries = load_gold(gold_path)

    with tempfile.TemporaryDirectory() as tmp:
        store, content_to_gold = build_store(corpus, Path(tmp) / "memory_eval.db")
        try:
            modes = {"nl": (lambda s: s), "or": or_expand}
            per_mode: Dict[str, List[Dict[str, Any]]] = {"nl": [], "or": []}

            for q in queries:
                relevant = set(q.get("relevant_fact_ids") or [])
                allowed = set(q.get("allowed_fact_ids") or [])
                for mode, transform in modes.items():
                    expanded = transform(q["query"])
                    ranked = retrieve(store, content_to_gold, expanded)
                    metrics = score_query(ranked, relevant, allowed)
                    per_mode[mode].append({
                        "id": q["id"],
                        "type": q.get("type", "unknown"),
                        "query": q["query"],
                        "expanded_query": expanded,
                        "relevant": sorted(relevant),
                        "ranked": ranked[:NDCG_K],
                        "metrics": metrics,
                    })
        finally:
            store.close()

    report: Dict[str, Any] = {
        "gold": gold_path,
        "num_corpus": len(corpus),
        "num_queries": len(queries),
        "by_type": _per_type_counts(queries),
        "modes": {},
    }
    for mode in ("nl", "or"):
        report["modes"][mode] = {
            "per_query": per_mode[mode],
            "aggregate": _aggregate(per_mode[mode]),
        }
    return report


def _per_type_counts(queries: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for q in queries:
        t = q.get("type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _print_report(report: Dict[str, Any], gate_mode: str, threshold: Optional[float],
                  gate_passed: Optional[bool]) -> None:
    print("=" * 76)
    print("Memory retrieval-quality eval (holographic FTS5 baseline, no merge layer yet)")
    print("=" * 76)
    print(f"gold: {report['gold']}")
    print(f"corpus facts: {report['num_corpus']}   queries: {report['num_queries']}")
    print(f"query types: " + ", ".join(f"{t}={n}" for t, n in sorted(report['by_type'].items())))
    print("-" * 76)

    # Aggregate table, NL vs OR side by side for the headline metrics.
    nl = report["modes"]["nl"]["aggregate"]
    orr = report["modes"]["or"]["aggregate"]
    headline = ["recall@1", "recall@3", "recall@5", "recall@10",
                "precision@1", "precision@5", "mrr", f"ndcg@{NDCG_K}"]
    print(f"{'metric':<14}{'raw NL':>12}{'OR-expanded':>16}")
    for key in headline:
        print(f"{key:<14}{nl[key]:>12.3f}{orr[key]:>16.3f}")
    print("-" * 76)

    # Per-query detail for the gated (OR) mode, so a regression is debuggable.
    print(f"per-query (mode={gate_mode}):")
    for pq in report["modes"][gate_mode]["per_query"]:
        m = pq["metrics"]
        if m.get("abstention"):
            mark = "OK " if m["abstained"] else "NOISE"
            print(f"  [{mark:>5}] {pq['type']:<16} {pq['query']!r} -> abstain "
                  f"(returned {m['num_returned']})")
        else:
            r5 = m["recall@5"]
            mark = "HIT " if m["hit_rate@5"] else "MISS"
            print(f"  [{mark:>5}] {pq['type']:<16} {pq['query']!r} -> "
                  f"recall@5={r5:.2f} mrr={m['mrr']:.2f} ranked={pq['ranked'][:3]}")
    print("-" * 76)
    print(f"finding: raw NL recall@5={nl['recall@5']:.3f} -> "
          f"OR-expanded recall@5={orr['recall@5']:.3f} "
          "(FTS5 ANDs terms; stopword-strip + OR is the lever).")

    if threshold is not None:
        gated = report["modes"][gate_mode]["aggregate"]["recall@5"]
        verdict = "GATE PASSED" if gate_passed else "GATE FAILED"
        print(f"gate: {gate_mode} recall@5 {gated:.3f} "
              f"{'>=' if gate_passed else '<'} threshold {threshold:.3f} -> {verdict}")
    print("=" * 76)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Memory retrieval-quality eval harness (CI gate, requirement #7)."
    )
    default_gold = str(Path(__file__).resolve().parent / "gold" / "memory_gold.yaml")
    ap.add_argument("--gold", default=default_gold,
                    help="Path to the frozen gold-set YAML (default: gold/memory_gold.yaml).")
    ap.add_argument("--threshold", type=float, default=None,
                    help="Min aggregate recall@5 to pass the gate (exit 1 below it).")
    ap.add_argument("--mode", choices=("nl", "or", "merged"), default="or",
                    help="Retrieval pass the gate scores. 'nl'/'or' score the FTS5 "
                         "baseline; 'merged' scores MergeLayer fused over the two "
                         "local planes (session + holographic). Default: or.")
    ap.add_argument("--json", action="store_true", help="Emit the full JSON report.")
    args = ap.parse_args(argv)

    merged = args.mode == "merged"
    try:
        report = run_merged_eval(args.gold) if merged else run_eval(args.gold)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2  # malformed gold / IO fails the gate, never a silent pass
    except Exception as exc:  # noqa: BLE001 - never crash CI with a raw traceback
        print(f"error: eval failed: {exc}", file=sys.stderr)
        return 2

    gate_passed: Optional[bool] = None
    if args.threshold is not None:
        gated = report["modes"][args.mode]["aggregate"]["recall@5"]
        gate_passed = gated >= args.threshold
        report["gate"] = {
            "mode": args.mode,
            "metric": "recall@5",
            "value": gated,
            "threshold": args.threshold,
            "passed": gate_passed,
        }

    if args.json:
        print(json.dumps(report, indent=2))
    elif merged:
        _print_merged_report(report, args.threshold, gate_passed)
    else:
        _print_report(report, args.mode, args.threshold, gate_passed)

    if gate_passed is None:
        return 0
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())
