"""Tests for the bi-temporal holographic fact store (Phase 3, step 5).

These prove the Decision C bi-temporal substrate added to
``plugins/memory/holographic/store.py`` WITHOUT changing existing behavior:

  (a) the schema migration on a LEGACY-shape DB (a ``facts`` table created
      WITHOUT the new columns) adds the columns, backfills
      ``ext_key``/``t_valid``/``source_store``, and PRESERVES the legacy row;
  (b) the migration is IDEMPOTENT (open the same DB twice, no error, no
      duplicate columns);
  (c) ``invalidate()`` sets ``t_invalid`` so the fact disappears from the
      default read-only search but REAPPEARS under ``as_of`` < ``t_invalid``;
  (d) ``supersede()`` invalidates the old fact, returns the new one by default,
      and the old fact is still recallable ``as_of`` before invalidation;
  (e) ``defer_enrichment=True`` writes a fact that is immediately findable by
      ``search_facts_readonly`` (the hot-write path);
  (f) the ``source_store`` namespace filter excludes a fact in another
      namespace.

Every store builds in a temp dir, so nothing real is mutated and the test is
safe to re-run after a restart.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from plugins.memory.holographic.store import (
    _DEFAULT_SOURCE_STORE,
    MemoryStore,
    _content_ext_key,
)


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "memory_store.db"))
    try:
        yield s
    finally:
        s.close()


def _facts_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()]


# ----------------------------------------------------------------------------
# (a) migration on a legacy-shape DB
# ----------------------------------------------------------------------------

def _make_legacy_db(path) -> None:
    """Create a pre-migration ``facts`` table (NO bi-temporal columns) + a row.

    Mirrors the original pre-Phase-3 schema shape (no ext_key / t_valid /
    t_invalid / supersedes_id / source_store). The legacy row is inserted with
    an explicit created_at so the t_valid backfill is observable.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE facts (
            fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL UNIQUE,
            category        TEXT DEFAULT 'general',
            tags            TEXT DEFAULT '',
            trust_score     REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count   INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hrr_vector      BLOB
        )
        """
    )
    conn.execute(
        """
        INSERT INTO facts (content, category, tags, trust_score, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "The legacy capital fact says Paris",
            "geo",
            "capital,france",
            0.7,
            "2020-01-01 00:00:00",
            "2020-01-01 00:00:00",
        ),
    )
    conn.commit()
    conn.close()


def test_migration_on_legacy_db_adds_columns_and_preserves_row(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)

    # Sanity: the legacy table genuinely lacks the new columns.
    pre = sqlite3.connect(str(db))
    pre_cols = _facts_columns(pre)
    pre.close()
    for col in ("ext_key", "t_valid", "t_invalid", "supersedes_id", "source_store"):
        assert col not in pre_cols, f"legacy db should not have {col}"

    # Opening MemoryStore runs _init_db -> _migrate_bitemporal.
    store = MemoryStore(db_path=str(db))
    try:
        cols = _facts_columns(store._conn)
        for col in ("ext_key", "t_valid", "t_invalid", "supersedes_id", "source_store"):
            assert col in cols, f"migration must add {col}"

        # The legacy row is PRESERVED (not deleted) and backfilled.
        row = store._conn.execute(
            "SELECT * FROM facts WHERE content = ?",
            ("The legacy capital fact says Paris",),
        ).fetchone()
        assert row is not None, "migration must preserve the existing row"
        assert row["category"] == "geo"
        assert row["trust_score"] == 0.7

        # ext_key backfilled deterministically from content (reproducible).
        assert row["ext_key"] == _content_ext_key("The legacy capital fact says Paris")
        # t_valid backfilled from created_at.
        assert row["t_valid"] == "2020-01-01 00:00:00"
        # source_store backfilled to the default self namespace.
        assert row["source_store"] == _DEFAULT_SOURCE_STORE
        # Still currently valid (not invalidated by the migration).
        assert row["t_invalid"] is None

        # UNIQUE index on ext_key exists.
        idx = {
            r[1]
            for r in store._conn.execute("PRAGMA index_list(facts)").fetchall()
        }
        assert "idx_facts_ext_key" in idx
    finally:
        store.close()


# ----------------------------------------------------------------------------
# (b) idempotency
# ----------------------------------------------------------------------------

def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)

    s1 = MemoryStore(db_path=str(db))
    cols_after_first = _facts_columns(s1._conn)
    s1.close()

    # Re-open: _init_db runs again. No error, no duplicate columns.
    s2 = MemoryStore(db_path=str(db))
    try:
        cols_after_second = _facts_columns(s2._conn)
        assert cols_after_first == cols_after_second
        # No duplicate column names.
        assert len(cols_after_second) == len(set(cols_after_second))
        # The single legacy row was not duplicated by re-running the backfill.
        n = s2._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        assert n == 1
    finally:
        s2.close()


def test_fresh_db_has_bitemporal_columns(store):
    cols = _facts_columns(store._conn)
    for col in ("ext_key", "t_valid", "t_invalid", "supersedes_id", "source_store"):
        assert col in cols


# ----------------------------------------------------------------------------
# (c) invalidate -> disappears from default, reappears as_of earlier
# ----------------------------------------------------------------------------

def test_invalidate_hides_from_default_but_visible_as_of(store):
    fid = store.add_fact("Pluto is the ninth planet of the solar system", category="astro")
    ext_key = store._conn.execute(
        "SELECT ext_key FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["ext_key"]

    # Visible by default before invalidation.
    before = store.search_facts_readonly("Pluto", min_trust=0.0, limit=10)
    assert any(r["fact_id"] == fid for r in before)

    t_valid = store._conn.execute(
        "SELECT t_valid FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["t_valid"]

    # Invalidate at an EXPLICIT timestamp strictly after t_valid so the
    # as_of-between-them window is well-defined and not subject to same-second
    # truncation against an implicit now().
    t_invalid = "2099-01-01 00:00:00"
    as_of_between = "2098-01-01 00:00:00"   # t_valid < as_of_between < t_invalid
    as_of_before_valid = "1990-01-01 00:00:00"  # before t_valid -> not yet valid

    assert store.invalidate(ext_key, t_invalid=t_invalid) is True

    # Gone from the default (currently-valid) view.
    after = store.search_facts_readonly("Pluto", min_trust=0.0, limit=10)
    assert all(r["fact_id"] != fid for r in after), (
        "an invalidated fact must not appear in the default readonly search"
    )

    # The row is NOT deleted (bi-temporal: t_invalid set, content preserved).
    row = store._conn.execute(
        "SELECT t_invalid, content FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()
    assert row is not None
    assert row["t_invalid"] == t_invalid
    assert row["content"] == "Pluto is the ninth planet of the solar system"

    # Reappears under as_of between t_valid and t_invalid (valid then, not yet
    # invalid then).
    assert t_valid < as_of_between < t_invalid
    as_of = store.search_facts_readonly(
        "Pluto", min_trust=0.0, limit=10, as_of=as_of_between
    )
    assert any(r["fact_id"] == fid for r in as_of), (
        "the fact must reappear under t_valid <= as_of < t_invalid"
    )

    # An as_of before the fact was valid excludes it (t_valid <= as_of is false).
    too_early = store.search_facts_readonly(
        "Pluto", min_trust=0.0, limit=10, as_of=as_of_before_valid
    )
    assert all(r["fact_id"] != fid for r in too_early)

    # Re-invalidating is a no-op (idempotent False).
    assert store.invalidate(ext_key) is False


# ----------------------------------------------------------------------------
# (d) supersede: old invalidated, new default, old recallable as_of
# ----------------------------------------------------------------------------

def test_supersede_recency_wins_and_old_recallable_as_of(store):
    old_fid = store.add_fact("The deploy region is us-east-1", category="config")
    old_ext = store._conn.execute(
        "SELECT ext_key FROM facts WHERE fact_id = ?", (old_fid,)
    ).fetchone()["ext_key"]
    old_t_valid = store._conn.execute(
        "SELECT t_valid FROM facts WHERE fact_id = ?", (old_fid,)
    ).fetchone()["t_valid"]

    # Ensure t_invalid is strictly greater than old_t_valid (one-second floor).
    time.sleep(1.1)
    new_ext = store.supersede(
        old_ext, "The deploy region is eu-central-1", category="config"
    )
    assert isinstance(new_ext, str) and new_ext

    # Default search returns the NEW fact, not the old one.
    new_default = store.search_facts_readonly("deploy region", min_trust=0.0, limit=10)
    contents = {r["content"] for r in new_default}
    assert "The deploy region is eu-central-1" in contents
    assert "The deploy region is us-east-1" not in contents, (
        "the superseded fact must not appear in the default view"
    )

    # The new fact links supersedes_id to the old ext_key.
    new_row = store._conn.execute(
        "SELECT supersedes_id FROM facts WHERE ext_key = ?", (new_ext,)
    ).fetchone()
    assert new_row["supersedes_id"] == old_ext

    # The old fact is invalidated, NOT deleted.
    old_row = store._conn.execute(
        "SELECT t_invalid, content FROM facts WHERE ext_key = ?", (old_ext,)
    ).fetchone()
    assert old_row is not None
    assert old_row["t_invalid"] is not None
    assert old_row["content"] == "The deploy region is us-east-1"

    # The old fact is still recallable as_of its valid time (before supersede).
    as_of_old = store.search_facts_readonly(
        "deploy region", min_trust=0.0, limit=10, as_of=old_t_valid
    )
    old_contents = {r["content"] for r in as_of_old}
    assert "The deploy region is us-east-1" in old_contents, (
        "the old fact must be recallable as_of a time before its invalidation"
    )


# ----------------------------------------------------------------------------
# (e) defer_enrichment hot write is immediately recallable
# ----------------------------------------------------------------------------

def test_defer_enrichment_write_is_immediately_findable(store):
    fid = store.add_fact(
        "The hot path config key is read_your_writes_fts",
        category="config",
        defer_enrichment=True,
    )
    assert fid > 0

    # Immediately recallable by FTS5 (the AFTER INSERT trigger indexed it).
    hits = store.search_facts_readonly(
        "read_your_writes_fts", min_trust=0.0, limit=10
    )
    assert any(r["fact_id"] == fid for r in hits), (
        "a defer_enrichment hot write must be immediately FTS5-recallable"
    )

    # Bi-temporal/namespace fields are still set on the deferred write.
    row = store._conn.execute(
        "SELECT ext_key, t_valid, t_invalid, source_store FROM facts WHERE fact_id = ?",
        (fid,),
    ).fetchone()
    assert row["ext_key"]
    assert row["t_valid"] is not None
    assert row["t_invalid"] is None
    assert row["source_store"] == _DEFAULT_SOURCE_STORE

    # Enrichment was skipped: no HRR vector and no entity links for this fact.
    assert row.keys()  # row exists
    hrr_blob = store._conn.execute(
        "SELECT hrr_vector FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["hrr_vector"]
    assert hrr_blob is None, "defer_enrichment must skip the HRR encode"
    links = store._conn.execute(
        "SELECT COUNT(*) AS n FROM fact_entities WHERE fact_id = ?", (fid,)
    ).fetchone()["n"]
    assert links == 0, "defer_enrichment must skip entity extraction/linking"


# ----------------------------------------------------------------------------
# (f) namespace (source_store) filter
# ----------------------------------------------------------------------------

def test_namespace_filter_excludes_other_namespace(store):
    self_fid = store.add_fact(
        "The orchestrator self namespace fact about widgets",
        category="misc",
        source_store="orchestrator/self",
    )
    shared_fid = store.add_fact(
        "The shared namespace fact about widgets",
        category="misc",
        source_store="orchestrator/shared",
    )

    # Requesting orchestrator/self returns ONLY the self fact.
    self_only = store.search_facts_readonly(
        "widgets", min_trust=0.0, limit=10, source_store="orchestrator/self"
    )
    ids = {r["fact_id"] for r in self_only}
    assert self_fid in ids
    assert shared_fid not in ids, (
        "a fact in orchestrator/shared must not be returned when "
        "source_store='orchestrator/self' is requested"
    )

    # Requesting orchestrator/shared returns ONLY the shared fact.
    shared_only = store.search_facts_readonly(
        "widgets", min_trust=0.0, limit=10, source_store="orchestrator/shared"
    )
    shared_ids = {r["fact_id"] for r in shared_only}
    assert shared_fid in shared_ids
    assert self_fid not in shared_ids

    # No namespace filter (default) returns both (namespace-agnostic back-compat).
    both = store.search_facts_readonly("widgets", min_trust=0.0, limit=10)
    both_ids = {r["fact_id"] for r in both}
    assert {self_fid, shared_fid} <= both_ids


# ----------------------------------------------------------------------------
# (g) ext_key collision safety: the store can NEVER brick (P0)
# ----------------------------------------------------------------------------
#
# _content_ext_key normalizes internal whitespace ("  ".join(split)) while the
# facts.content column is UNIQUE WITHOUT normalization. So two distinct stored
# rows that differ ONLY in whitespace derive the SAME content-hash ext_key. The
# UNIQUE index idx_facts_ext_key must NOT be allowed to raise IntegrityError and
# permanently un-openable the store; and add_fact of a new whitespace-variant
# content must NOT crash on an ext_key collision.

# A whitespace-variant pair: identical after " ".join(split()), distinct as raw
# content (extra internal space), so SAME ext_key, DIFFERENT content.
_WS_A = "the  port is 8000"   # two spaces between "the" and "port"
_WS_B = "the port is 8000"    # one space


def _assert_same_ext_key_distinct_content() -> None:
    """Guard: the pair really does collide (same key) while staying distinct."""
    assert _WS_A != _WS_B, "the pair must be distinct raw content"
    assert _content_ext_key(_WS_A) == _content_ext_key(_WS_B), (
        "the whitespace-variant pair must derive the SAME content-hash ext_key "
        "(otherwise this test does not exercise the collision)"
    )


def _make_legacy_db_with_two_whitespace_variants(path) -> None:
    """Create a pre-migration ``facts`` table with TWO whitespace-variant rows.

    Both rows are valid distinct content (the content UNIQUE constraint permits
    them, since they differ by an internal space), and both would derive the
    SAME ext_key from ``_content_ext_key`` on backfill -- the exact P0 collision
    that bricked the migration before the fix.
    """
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE facts (
            fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
            content         TEXT NOT NULL UNIQUE,
            category        TEXT DEFAULT 'general',
            tags            TEXT DEFAULT '',
            trust_score     REAL DEFAULT 0.5,
            retrieval_count INTEGER DEFAULT 0,
            helpful_count   INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            hrr_vector      BLOB
        )
        """
    )
    for content in (_WS_A, _WS_B):
        conn.execute(
            """
            INSERT INTO facts (content, category, created_at, updated_at)
            VALUES (?, 'config', '2020-01-01 00:00:00', '2020-01-01 00:00:00')
            """,
            (content,),
        )
    conn.commit()
    conn.close()


def test_migration_survives_ext_key_collision_and_stays_openable(tmp_path):
    """(a) A legacy DB with TWO whitespace-variant rows migrates successfully and
    stays openable: open MemoryStore twice with no exception, both rows present
    with DISTINCT ext_keys."""
    _assert_same_ext_key_distinct_content()

    db = tmp_path / "collision_legacy.db"
    _make_legacy_db_with_two_whitespace_variants(db)

    # First open: _init_db -> _migrate_bitemporal must NOT raise even though the
    # two rows collide on the content-derived ext_key.
    s1 = MemoryStore(db_path=str(db))
    try:
        rows = s1._conn.execute(
            "SELECT content, ext_key FROM facts ORDER BY fact_id"
        ).fetchall()
        # Both whitespace-variant rows are preserved (never dropped).
        contents = {r["content"] for r in rows}
        assert contents == {_WS_A, _WS_B}, "both whitespace-variant rows must survive"
        # Their ext_keys were disambiguated to be DISTINCT (the UNIQUE index holds).
        ext_keys = [r["ext_key"] for r in rows]
        assert all(k for k in ext_keys), "every row must have a non-null ext_key"
        assert len(set(ext_keys)) == 2, (
            "the two colliding rows must end with DISTINCT ext_keys"
        )
        # The UNIQUE index genuinely exists (it was created without raising).
        idx = {r[1] for r in s1._conn.execute("PRAGMA index_list(facts)").fetchall()}
        assert "idx_facts_ext_key" in idx
    finally:
        s1.close()

    # Second open: the store is NOT bricked -- re-running the migration over an
    # already-disambiguated DB is a clean no-op and never re-collides.
    s2 = MemoryStore(db_path=str(db))
    try:
        rows2 = s2._conn.execute(
            "SELECT content, ext_key FROM facts ORDER BY fact_id"
        ).fetchall()
        assert {r["content"] for r in rows2} == {_WS_A, _WS_B}
        assert len({r["ext_key"] for r in rows2}) == 2
        # No row was duplicated by re-running the backfill.
        n = s2._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        assert n == 2
    finally:
        s2.close()


def test_add_fact_whitespace_variants_both_succeed_with_distinct_keys(store):
    """(b) add_fact of two whitespace-variant NEW contents both succeed with
    distinct ext_keys and distinct fact_ids (no crash, no None return)."""
    _assert_same_ext_key_distinct_content()

    fid_a = store.add_fact(_WS_A, category="config")
    # The second add has NEW content but a COLLIDING content-derived ext_key.
    # It must NOT crash and must NOT dedup to the first row.
    fid_b = store.add_fact(_WS_B, category="config")

    assert isinstance(fid_a, int) and fid_a > 0
    assert isinstance(fid_b, int) and fid_b > 0
    assert fid_a != fid_b, (
        "two distinct whitespace-variant contents must be two distinct facts"
    )

    rows = store._conn.execute(
        "SELECT fact_id, content, ext_key FROM facts WHERE fact_id IN (?, ?)",
        (fid_a, fid_b),
    ).fetchall()
    assert len(rows) == 2, "both whitespace-variant facts must be stored"
    by_id = {r["fact_id"]: r for r in rows}
    assert by_id[fid_a]["content"] == _WS_A
    assert by_id[fid_b]["content"] == _WS_B
    ext_keys = {r["ext_key"] for r in rows}
    assert all(k for k in ext_keys), "every stored fact must have a non-null ext_key"
    assert len(ext_keys) == 2, "the two facts must have DISTINCT ext_keys"

    # Re-adding the exact same content dedups (content UNIQUE), proving the
    # ext_key-collision retry did not turn the dedup path off.
    assert store.add_fact(_WS_A, category="config") == fid_a
    assert store.add_fact(_WS_B, category="config") == fid_b
