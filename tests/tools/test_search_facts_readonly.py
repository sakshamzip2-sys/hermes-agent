"""Tests for MemoryStore.search_facts_readonly (Phase 3, step 2).

These prove the net-new read-only recall variant that fixes the C-1 read-write
contention and the NL implicit-AND recall gap, WITHOUT changing any existing
behavior:

  (a) it returns the same hits as search_facts for a known fact;
  (b) it does NOT increment retrieval_count (pure read);
  (c) an OR-expanded NL multi-word query recalls a fact that the raw
      implicit-AND query misses (the 0.62 -> 1.00 effect);
  (d) it works on a separate read-only connection while the single write
      connection is open and mid-transaction.

The store builds in a temp dir, so nothing real is mutated and the test is safe
to re-run after a restart.
"""

from __future__ import annotations

import sqlite3

import pytest

from plugins.memory.holographic.store import (
    MemoryStore,
    _or_expand_query,
)


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "memory_store.db"))
    try:
        yield s
    finally:
        s.close()


def _retrieval_count(store: MemoryStore, fact_id: int) -> int:
    row = store._conn.execute(
        "SELECT retrieval_count FROM facts WHERE fact_id = ?", (fact_id,)
    ).fetchone()
    return int(row["retrieval_count"])


# ----------------------------------------------------------------------------
# (a) same hits as search_facts for a known fact
# ----------------------------------------------------------------------------

def test_readonly_returns_same_hits_as_search_facts(store):
    fid = store.add_fact("The capital of France is Paris", category="geo")
    store.add_fact("My favorite programming language is Rust", category="prefs")

    writing = store.search_facts("Paris", min_trust=0.0, limit=10)
    readonly = store.search_facts_readonly("Paris", min_trust=0.0, limit=10)

    assert [r["fact_id"] for r in writing] == [r["fact_id"] for r in readonly]
    assert readonly, "expected at least one hit for a known fact"
    assert readonly[0]["fact_id"] == fid
    # Content payloads match too (it is the SAME select).
    assert writing[0]["content"] == readonly[0]["content"]


def test_readonly_respects_category_and_min_trust_filters(store):
    fid_geo = store.add_fact("Mount Everest is the tallest mountain", category="geo")
    store.add_fact("Everest catalog SKU 4471 in the warehouse", category="inventory")

    geo_only = store.search_facts_readonly(
        "Everest", category="geo", min_trust=0.0, limit=10
    )
    assert [r["fact_id"] for r in geo_only] == [fid_geo]

    # A min_trust above the default 0.5 floors everything out.
    none = store.search_facts_readonly("Everest", min_trust=0.9, limit=10)
    assert none == []


# ----------------------------------------------------------------------------
# (b) does NOT increment retrieval_count
# ----------------------------------------------------------------------------

def test_readonly_does_not_increment_retrieval_count(store):
    fid = store.add_fact("The mitochondria is the powerhouse of the cell")

    before = _retrieval_count(store, fid)
    for _ in range(5):
        hits = store.search_facts_readonly("mitochondria", min_trust=0.0, limit=10)
        assert any(r["fact_id"] == fid for r in hits)
    after = _retrieval_count(store, fid)

    assert before == after, "readonly search must not write retrieval_count"


def test_writing_search_still_increments_for_backcompat(store):
    """search_facts is unchanged: it must STILL increment (no regression)."""
    fid = store.add_fact("Saturn has the most prominent ring system")

    before = _retrieval_count(store, fid)
    store.search_facts("Saturn", min_trust=0.0, limit=10)
    after = _retrieval_count(store, fid)

    assert after == before + 1


# ----------------------------------------------------------------------------
# (c) OR-expansion recalls what implicit-AND misses
# ----------------------------------------------------------------------------

def test_or_expand_query_helper():
    # Stopwords dropped, surviving terms OR-joined, lowercased; the apostrophe
    # fragment "s" from "dog's" is dropped as a single-char token.
    assert _or_expand_query("What is my dog's name") == "dog OR name"
    # Plain multi-word query, no apostrophe.
    assert _or_expand_query("Hetzner deploy production") == "hetzner OR deploy OR production"
    # All-stopword / punctuation query falls back to the original.
    assert _or_expand_query("what is the") == "what is the"
    assert _or_expand_query("   ") == ""


def test_nl_query_misses_but_or_expansion_hits(store):
    # Stored fact lacks the NL filler words ("what", "is", "my", "name").
    fid = store.add_fact("Biscuit is a golden retriever dog")

    # Raw NL multi-word query: FTS5 implicit-ANDs every term, so the filler
    # words that are absent from the fact force a miss.
    nl = "what is my dog name"
    raw = store.search_facts_readonly(nl, min_trust=0.0, limit=10, or_expand=False)
    assert all(r["fact_id"] != fid for r in raw), (
        "implicit-AND NL query should miss the fact (no filler words stored)"
    )

    # Same query, OR-expanded internally: the surviving content terms recall it.
    expanded = store.search_facts_readonly(
        nl, min_trust=0.0, limit=10, or_expand=True
    )
    assert any(r["fact_id"] == fid for r in expanded), (
        "OR-expanded NL query must recall the fact (the 0.62 -> 1.00 effect)"
    )

    # Passing an already-expanded query (caller-side expansion) works too.
    pre_expanded = store.search_facts_readonly(
        _or_expand_query(nl), min_trust=0.0, limit=10, or_expand=False
    )
    assert any(r["fact_id"] == fid for r in pre_expanded)


# ----------------------------------------------------------------------------
# (d) works on a ro connection while the write connection is open
# ----------------------------------------------------------------------------

def test_readonly_uses_separate_ro_connection(store):
    store.add_fact("Helium is the second element on the periodic table")

    # First read-only call lazily opens the ro connection.
    store.search_facts_readonly("Helium", min_trust=0.0, limit=10)

    read_conn = store._read_conn
    assert read_conn is not None
    # It is a distinct object from the write connection (ro open succeeded on
    # this platform's sqlite build).
    assert read_conn is not store._conn

    # The ro connection truly cannot write.
    with pytest.raises(sqlite3.OperationalError):
        read_conn.execute(
            "UPDATE facts SET trust_score = 0.99 WHERE 1=1"
        )


def test_readonly_works_while_write_txn_open(store):
    fid = store.add_fact("Tokyo is the capital of Japan")

    # Open an explicit write transaction on the write connection and leave it
    # uncommitted, holding the writer. WAL still lets the ro connection read.
    store._conn.execute("BEGIN")
    store._conn.execute(
        "INSERT INTO facts (content, category) VALUES (?, ?)",
        ("Pending uncommitted row about Berlin", "geo"),
    )
    try:
        hits = store.search_facts_readonly("Tokyo", min_trust=0.0, limit=10)
        assert any(r["fact_id"] == fid for r in hits), (
            "ro recall must succeed concurrently with an open write txn"
        )
        # The uncommitted row is not visible to the separate ro connection.
        berlin = store.search_facts_readonly("Berlin", min_trust=0.0, limit=10)
        assert berlin == []
    finally:
        store._conn.rollback()
