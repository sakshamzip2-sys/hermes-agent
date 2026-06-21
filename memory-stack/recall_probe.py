#!/usr/bin/env python3
"""Memory recall eval probe (built-in FTS5 session-search layer).

The memory-stack audit scored *eval* a FAIL (1/10): nothing measured whether
the right memory actually surfaces for a query. This is a small, deterministic
probe that does. It seeds a corpus of fact + distractor messages into a temp
SessionDB and runs a ground-truth query set through the real FTS5
``search_messages`` retrieval, reporting precision@1, recall@5, and MRR.

It is intentionally self-contained (temp DB, no external providers) so it runs
anywhere. The semantic provider layers (Honcho, GBrain) need live services;
this harness is the deterministic floor and the shape to extend to them.

Run:  .venv/bin/python memory-stack/recall_probe.py
Exits non-zero if recall@5 drops below RECALL_FLOOR (a regression gate).
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Import the real session store.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hermes_state import SessionDB  # noqa: E402

RECALL_FLOOR = 0.8  # gate: at least 80% of facts must be retrievable in top-5

# (fact_id, message text the user "told" the agent)
FACTS = [
    ("rust", "My favorite programming language is Rust for systems work."),
    ("hetzner", "I deploy all my production services on Hetzner Cloud VMs."),
    ("dog", "My dog's name is Biscuit, a golden retriever."),
    ("darkmode", "I always prefer dark mode in my code editors."),
    ("rotation", "My API key rotation policy is every ninety days."),
    ("cricket", "My favorite sport is cricket and I play on weekends."),
    ("mango", "My favorite fruit is mango, especially Alphonso."),
    ("timezone", "I work in the India Standard Time zone, UTC plus five thirty."),
]

# Distractor messages — plausible but irrelevant to any probe query.
DISTRACTORS = [
    "The weather in Bangalore is pleasant this week.",
    "Let's schedule the standup for tomorrow morning.",
    "The build pipeline finished in twelve minutes.",
    "Remember to review the quarterly budget report.",
    "The conference talk was about distributed systems.",
]

# (query, expected fact_id) — what a later session would ask.
QUERIES = [
    ("favorite programming language", "rust"),
    ("Hetzner deploy production", "hetzner"),
    ("what is my dog name", "dog"),
    ("editor dark mode preference", "darkmode"),
    ("API key rotation policy", "rotation"),
    ("favorite sport weekends", "cricket"),
    ("favorite fruit", "mango"),
    ("which timezone do I work in", "timezone"),
]


def build_corpus(db: SessionDB) -> dict[int, str]:
    """Seed facts + distractors across a few sessions. Returns row_id -> fact_id."""
    row_to_fact: dict[int, str] = {}
    db.create_session("probe-facts", "api_server")
    for fact_id, text in FACTS:
        rid = db.append_message("probe-facts", "user", content=text)
        row_to_fact[rid] = fact_id
    db.create_session("probe-noise", "api_server")
    for text in DISTRACTORS:
        db.append_message("probe-noise", "user", content=text)
    return row_to_fact


# FTS5 implicitly ANDs query terms, so natural-language filler words ("what
# is my …") force a miss when the stored fact lacks them. A good recall layer
# strips stopwords and ORs the rest; the probe measures the lift this gives.
_STOPWORDS = {
    "what", "is", "my", "do", "i", "the", "a", "an", "in", "of", "to",
    "where", "which", "are", "you", "does", "how", "me", "on",
}


def or_preprocess(query: str) -> str:
    terms = [t for t in query.lower().split() if t not in _STOPWORDS]
    return " OR ".join(terms) if terms else query


def evaluate(db: SessionDB, row_to_fact: dict[int, str], transform=None) -> dict:
    per_query = []
    hits_at_1 = 0
    recalled = 0
    rr_sum = 0.0

    for query, expected in QUERIES:
        q = transform(query) if transform else query
        results = db.search_messages(q, role_filter=["user"], limit=5)
        # Map each result back to its fact_id (by message row id when present,
        # else by content match) and find the rank of the expected fact.
        ranked_facts: list[str] = []
        for r in results:
            rid = r.get("message_id") or r.get("id") or r.get("rowid")
            fact = row_to_fact.get(rid)
            if fact is None:
                snippet = (r.get("content") or r.get("snippet") or "").lower()
                for fid, ftext in FACTS:
                    if ftext.lower()[:25] in snippet or fid in snippet:
                        fact = fid
                        break
            ranked_facts.append(fact or "?")

        rank = ranked_facts.index(expected) + 1 if expected in ranked_facts else 0
        at1 = rank == 1
        found = rank != 0
        hits_at_1 += int(at1)
        recalled += int(found)
        rr_sum += (1.0 / rank) if rank else 0.0
        per_query.append((query, expected, rank, ranked_facts[:3]))

    n = len(QUERIES)
    return {
        "n": n,
        "precision_at_1": hits_at_1 / n,
        "recall_at_5": recalled / n,
        "mrr": rr_sum / n,
        "per_query": per_query,
    }


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db = SessionDB(db_path=Path(tmp) / "probe.db")
        if not getattr(db, "_fts_enabled", False):
            print("FTS5 not available in this SQLite build — cannot run probe.")
            return 2
        row_to_fact = build_corpus(db)
        raw = evaluate(db, row_to_fact, transform=None)
        proc = evaluate(db, row_to_fact, transform=or_preprocess)

    print("=" * 64)
    print("Memory recall probe — built-in FTS5 session search")
    print("=" * 64)
    print(f"facts seeded: {len(FACTS)}   distractors: {len(DISTRACTORS)}   queries: {raw['n']}")
    print("-" * 64)
    print(f"{'metric':<14}{'raw NL query':>16}{'OR-preprocessed':>18}")
    print(f"{'precision@1':<14}{raw['precision_at_1']:>16.2f}{proc['precision_at_1']:>18.2f}")
    print(f"{'recall@5':<14}{raw['recall_at_5']:>16.2f}{proc['recall_at_5']:>18.2f}")
    print(f"{'MRR':<14}{raw['mrr']:>16.2f}{proc['mrr']:>18.2f}")
    print("-" * 64)
    print("per-query (OR-preprocessed):")
    for query, expected, rank, top3 in proc["per_query"]:
        status = f"rank {rank}" if rank else "MISS"
        print(f"  [{status:>6}] {query!r} -> {expected!r}")
    print("=" * 64)
    print(
        f"finding: raw NL recall@5={raw['recall_at_5']:.2f} → "
        f"OR-preprocessed={proc['recall_at_5']:.2f} "
        "(FTS5 ANDs terms; stopword-strip + OR is the lever)."
    )

    if proc["recall_at_5"] < RECALL_FLOOR:
        print(f"FAIL: preprocessed recall@5 {proc['recall_at_5']:.2f} < floor {RECALL_FLOOR}")
        return 1
    print(f"PASS: preprocessed recall@5 {proc['recall_at_5']:.2f} >= floor {RECALL_FLOOR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
