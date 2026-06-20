"""Tests for archive-not-delete retention on the holographic fact store (req #9).

These prove the Decision C / MEMORY-POLICY retention substrate added to
``plugins/memory/holographic/store.py`` WITHOUT any data-loss path and WITHOUT
changing existing behavior:

  (a) ``archive_fact`` hides a fact from the default ``search_facts_readonly``
      view and ``restore_fact`` brings it back -- reversible, content intact, NO
      data loss (the row is preserved, never deleted);
  (b) the migration adds ``archived_at`` additively + idempotently, legacy rows
      preserved (and default to active);
  (c) ``select_eviction_candidates`` returns only policy-matching facts and is
      read-only (no archiving side effect);
  (d) an INVALIDATED fact is archivable but still recallable ``as_of`` (the
      bi-temporal history is preserved across an archive).

Plus the bounded-growth helper:
  (e) ``select_facts_over_capacity`` returns exactly the lowest-value overflow
      and is a pure read.

Every store builds in a temp dir, so nothing real is mutated and the test is
safe to re-run after a restart.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

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


def _ext_key_of(store: MemoryStore, fact_id: int) -> str:
    return store._conn.execute(
        "SELECT ext_key FROM facts WHERE fact_id = ?", (fact_id,)
    ).fetchone()["ext_key"]


def _backdate(store: MemoryStore, fact_id: int, days_ago: float) -> None:
    """Force a fact's created_at to ``days_ago`` days in the past (test aging)."""
    ts = (
        datetime.now(timezone.utc) - timedelta(days=days_ago)
    ).strftime("%Y-%m-%d %H:%M:%S")
    store._conn.execute(
        "UPDATE facts SET created_at = ? WHERE fact_id = ?", (ts, fact_id)
    )
    store._conn.commit()


# ----------------------------------------------------------------------------
# (a) archive hides from default search; restore brings it back; content intact
# ----------------------------------------------------------------------------

def test_archive_hides_then_restore_brings_back_no_data_loss(store):
    fid = store.add_fact(
        "The staging deploy token rotates every Monday", category="ops"
    )
    ext_key = _ext_key_of(store, fid)

    # Visible by default before archiving.
    before = store.search_facts_readonly("staging deploy", min_trust=0.0, limit=10)
    assert any(r["fact_id"] == fid for r in before)

    # Archive -> gone from the DEFAULT view.
    assert store.archive_fact(ext_key) is True
    after = store.search_facts_readonly("staging deploy", min_trust=0.0, limit=10)
    assert all(r["fact_id"] != fid for r in after), (
        "an archived fact must not appear in the default readonly search"
    )

    # The row is NOT deleted: it is preserved with content intact and
    # archived_at set (a reversible state, not a hard delete).
    row = store._conn.execute(
        "SELECT content, archived_at FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()
    assert row is not None, "archiving must NOT delete the row (no data loss)"
    assert row["content"] == "The staging deploy token rotates every Monday"
    assert row["archived_at"] is not None

    # include_archived=True still finds it (the archive is searchable on demand).
    incl = store.search_facts_readonly(
        "staging deploy", min_trust=0.0, limit=10, include_archived=True
    )
    assert any(r["fact_id"] == fid for r in incl), (
        "include_archived=True must surface the archived fact"
    )

    # Restore -> back in the default view, content unchanged (reversible).
    assert store.restore_fact(ext_key) is True
    restored = store.search_facts_readonly(
        "staging deploy", min_trust=0.0, limit=10
    )
    assert any(r["fact_id"] == fid for r in restored), (
        "restore_fact must bring the fact back into the default view"
    )
    row2 = store._conn.execute(
        "SELECT content, archived_at FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()
    assert row2["content"] == "The staging deploy token rotates every Monday"
    assert row2["archived_at"] is None, "restore must clear archived_at"


def test_archive_and_restore_are_idempotent(store):
    fid = store.add_fact("A throwaway fact about widgets", category="misc")
    ext_key = _ext_key_of(store, fid)

    # First archive succeeds; re-archiving is a no-op False.
    assert store.archive_fact(ext_key) is True
    assert store.archive_fact(ext_key) is False

    # First restore succeeds; restoring an already-active fact is a no-op False.
    assert store.restore_fact(ext_key) is True
    assert store.restore_fact(ext_key) is False

    # Archiving / restoring an unknown ext_key never raises, returns False.
    assert store.archive_fact("does-not-exist") is False
    assert store.restore_fact("does-not-exist") is False


# ----------------------------------------------------------------------------
# (b) migration adds archived_at additively + idempotently; legacy rows kept
# ----------------------------------------------------------------------------

def _make_legacy_db(path) -> None:
    """Create a pre-retention ``facts`` table (NO archived_at column) + a row."""
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
        INSERT INTO facts (content, category, trust_score, created_at, updated_at)
        VALUES (?, 'geo', 0.7, '2020-01-01 00:00:00', '2020-01-01 00:00:00')
        """,
        ("The legacy capital fact says Paris",),
    )
    conn.commit()
    conn.close()


def test_retention_migration_additive_idempotent_legacy_preserved(tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)

    # Sanity: the legacy table genuinely lacks archived_at.
    pre = sqlite3.connect(str(db))
    assert "archived_at" not in _facts_columns(pre)
    pre.close()

    # First open runs _init_db -> _migrate_retention.
    s1 = MemoryStore(db_path=str(db))
    try:
        cols1 = _facts_columns(s1._conn)
        assert "archived_at" in cols1, "migration must add archived_at"

        # The legacy row is PRESERVED and defaults to ACTIVE (archived_at NULL),
        # so it recalls exactly as before until something archives it.
        legacy = s1._conn.execute(
            "SELECT content, archived_at, trust_score FROM facts WHERE content = ?",
            ("The legacy capital fact says Paris",),
        ).fetchone()
        assert legacy is not None, "migration must preserve the legacy row"
        assert legacy["archived_at"] is None, "legacy rows default to active"
        assert legacy["trust_score"] == 0.7

        # The legacy fact is recallable in the default view (active by default).
        hits = s1._conn.execute(
            "SELECT 1 FROM facts WHERE archived_at IS NULL AND content LIKE '%Paris%'"
        ).fetchall()
        assert hits, "an un-archived legacy fact stays active/visible"
    finally:
        s1.close()

    # Second open: idempotent -- no error, no duplicate column, row not dup'd.
    s2 = MemoryStore(db_path=str(db))
    try:
        cols2 = _facts_columns(s2._conn)
        assert cols2.count("archived_at") == 1, "no duplicate archived_at column"
        assert cols1 == cols2
        n = s2._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        assert n == 1, "re-running the migration must not duplicate rows"
    finally:
        s2.close()


def test_fresh_db_has_archived_at_column(store):
    assert "archived_at" in _facts_columns(store._conn)
    # A freshly added fact is active by default.
    fid = store.add_fact("A brand new fact", category="misc")
    row = store._conn.execute(
        "SELECT archived_at FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()
    assert row["archived_at"] is None


# ----------------------------------------------------------------------------
# (c) select_eviction_candidates returns only policy-matching facts; read-only
# ----------------------------------------------------------------------------

def test_select_eviction_candidates_only_policy_matches_and_read_only(store):
    # KEEP: high trust (above min_trust) even though old + unretrieved.
    keep_high_trust = store.add_fact("High trust durable fact", category="keep")
    store.update_fact(keep_high_trust, trust_delta=0.5)   # 0.5 -> 1.0
    _backdate(store, keep_high_trust, days_ago=365)

    # KEEP: low trust + old BUT has been retrieved (retrieval_count > 0).
    keep_retrieved = store.add_fact("Low trust but recalled fact", category="keep")
    store.update_fact(keep_retrieved, trust_delta=-0.5)   # 0.5 -> 0.0
    _backdate(store, keep_retrieved, days_ago=365)
    store._conn.execute(
        "UPDATE facts SET retrieval_count = 3 WHERE fact_id = ?", (keep_retrieved,)
    )
    store._conn.commit()

    # KEEP: low trust + unretrieved BUT recent (younger than max_age_days).
    keep_recent = store.add_fact("Low trust but recent fact", category="keep")
    store.update_fact(keep_recent, trust_delta=-0.5)      # 0.5 -> 0.0

    # EVICT: low trust AND zero retrieval AND aged.
    evict_aged = store.add_fact("Low trust old unrecalled junk", category="junk")
    store.update_fact(evict_aged, trust_delta=-0.4)       # 0.5 -> 0.1
    _backdate(store, evict_aged, days_ago=365)

    cands = store.select_eviction_candidates(
        min_trust=0.3,
        max_age_days=90.0,
        require_zero_retrieval=True,
        include_invalidated=True,
    )
    ext_keys = {c["ext_key"] for c in cands}

    # Only the genuine low-value-and-aged fact is a candidate.
    assert _ext_key_of(store, evict_aged) in ext_keys
    assert _ext_key_of(store, keep_high_trust) not in ext_keys
    assert _ext_key_of(store, keep_retrieved) not in ext_keys
    assert _ext_key_of(store, keep_recent) not in ext_keys

    # READ-ONLY: nothing was archived by the selector (the caller decides).
    for fid in (keep_high_trust, keep_retrieved, keep_recent, evict_aged):
        archived = store._conn.execute(
            "SELECT archived_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["archived_at"]
        assert archived is None, (
            "select_eviction_candidates must NOT archive (pure read)"
        )

    # include_invalidated=True also surfaces an invalidated fact.
    inv = store.add_fact("This will be superseded", category="config")
    inv_ext = _ext_key_of(store, inv)
    store.invalidate(inv_ext)
    with_inv = store.select_eviction_candidates(include_invalidated=True)
    assert inv_ext in {c["ext_key"] for c in with_inv}
    # include_invalidated=False excludes it (only the aged junk remains).
    without_inv = store.select_eviction_candidates(include_invalidated=False)
    assert inv_ext not in {c["ext_key"] for c in without_inv}


def test_select_eviction_candidates_excludes_already_archived(store):
    fid = store.add_fact("Aged low-value fact already archived", category="junk")
    store.update_fact(fid, trust_delta=-0.4)
    _backdate(store, fid, days_ago=365)
    ext_key = _ext_key_of(store, fid)

    # It is a candidate before archiving.
    assert ext_key in {c["ext_key"] for c in store.select_eviction_candidates()}

    # Once archived, it is no longer returned (already evicted).
    assert store.archive_fact(ext_key) is True
    assert ext_key not in {c["ext_key"] for c in store.select_eviction_candidates()}


# ----------------------------------------------------------------------------
# (d) an invalidated fact is archivable but still recallable as_of (bi-temporal)
# ----------------------------------------------------------------------------

def test_invalidated_fact_is_archivable_but_recallable_as_of(store):
    fid = store.add_fact("The deploy region is us-east-1", category="config")
    ext_key = _ext_key_of(store, fid)
    t_valid = store._conn.execute(
        "SELECT t_valid FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["t_valid"]

    # Invalidate at an explicit future ts so the as_of window is well-defined.
    t_invalid = "2099-01-01 00:00:00"
    as_of_between = "2098-01-01 00:00:00"   # t_valid < as_of_between < t_invalid
    assert store.invalidate(ext_key, t_invalid=t_invalid) is True

    # Archiving an invalidated fact succeeds (it is a valid eviction target).
    assert store.archive_fact(ext_key) is True

    # Even archived AND invalidated, the bi-temporal history is preserved:
    # the fact is still recallable as_of a time when it was valid, PROVIDED the
    # archive is included (an archive is a default-view hide, not a delete).
    assert t_valid < as_of_between < t_invalid
    recalled = store.search_facts_readonly(
        "deploy region",
        min_trust=0.0,
        limit=10,
        as_of=as_of_between,
        include_archived=True,
    )
    assert any(r["fact_id"] == fid for r in recalled), (
        "an archived+invalidated fact must still be recallable as_of with "
        "include_archived=True (bi-temporal history preserved, no data loss)"
    )

    # The row, content, and bi-temporal fields all survive the archive.
    row = store._conn.execute(
        "SELECT content, t_invalid, archived_at FROM facts WHERE fact_id = ?",
        (fid,),
    ).fetchone()
    assert row["content"] == "The deploy region is us-east-1"
    assert row["t_invalid"] == t_invalid
    assert row["archived_at"] is not None

    # And it is fully restorable (archive is reversible even for invalidated rows).
    assert store.restore_fact(ext_key) is True
    assert store._conn.execute(
        "SELECT archived_at FROM facts WHERE fact_id = ?", (fid,)
    ).fetchone()["archived_at"] is None


# ----------------------------------------------------------------------------
# (e) bounded-growth helper returns the lowest-value overflow; pure read
# ----------------------------------------------------------------------------

def test_select_facts_over_capacity_returns_lowest_value_overflow(store):
    # Five active facts with distinct trust so the value ordering is unambiguous.
    ids: list[int] = []
    for i, trust_delta in enumerate([0.4, 0.2, 0.0, -0.2, -0.4]):
        # base trust 0.5 -> {0.9, 0.7, 0.5, 0.3, 0.1}
        fid = store.add_fact(f"Capacity fact number {i}", category="cap")
        store.update_fact(fid, trust_delta=trust_delta)
        ids.append(fid)

    # Cap at 3 active facts -> overflow is the 2 lowest-value facts.
    over = store.select_facts_over_capacity(max_active_facts=3)
    assert len(over) == 2, "overflow must be active_count - cap"
    over_ext = {c["ext_key"] for c in over}
    # The two lowest-trust facts (0.1, 0.3) are the overflow.
    assert _ext_key_of(store, ids[4]) in over_ext   # trust 0.1
    assert _ext_key_of(store, ids[3]) in over_ext   # trust 0.3
    # The higher-trust facts are kept.
    assert _ext_key_of(store, ids[0]) not in over_ext

    # Lowest-value-first ordering.
    assert over[0]["trust_score"] <= over[1]["trust_score"]

    # PURE READ: nothing was archived.
    for fid in ids:
        assert store._conn.execute(
            "SELECT archived_at FROM facts WHERE fact_id = ?", (fid,)
        ).fetchone()["archived_at"] is None

    # At/under the cap -> empty (nothing to evict). max<=0 -> unbounded (empty).
    assert store.select_facts_over_capacity(max_active_facts=5) == []
    assert store.select_facts_over_capacity(max_active_facts=10) == []
    assert store.select_facts_over_capacity(max_active_facts=0) == []


def test_over_capacity_ignores_archived_and_invalidated(store):
    # Three valid+active facts, plus one archived and one invalidated that must
    # NOT count toward the active working set.
    keep1 = store.add_fact("Active fact one", category="cap")
    keep2 = store.add_fact("Active fact two", category="cap")
    keep3 = store.add_fact("Active fact three", category="cap")

    archived = store.add_fact("Already archived fact", category="cap")
    store.archive_fact(_ext_key_of(store, archived))

    invalidated = store.add_fact("Already invalidated fact", category="cap")
    store.invalidate(_ext_key_of(store, invalidated))

    # Only 3 active+valid facts. A cap of 3 -> no overflow even though 5 rows
    # exist, because archived and invalidated rows are not in the working set.
    assert store.select_facts_over_capacity(max_active_facts=3) == []

    # A cap of 2 -> exactly 1 overflow, drawn from the active+valid set only.
    over = store.select_facts_over_capacity(max_active_facts=2)
    assert len(over) == 1
    over_ext = over[0]["ext_key"]
    assert over_ext in {
        _ext_key_of(store, keep1),
        _ext_key_of(store, keep2),
        _ext_key_of(store, keep3),
    }
    assert over_ext != _ext_key_of(store, archived)
    assert over_ext != _ext_key_of(store, invalidated)


def test_over_capacity_scoped_to_namespace(store):
    a1 = store.add_fact("ns a fact one", category="cap", source_store="agent/a")
    a2 = store.add_fact("ns a fact two", category="cap", source_store="agent/a")
    store.add_fact("ns b fact one", category="cap", source_store="agent/b")

    # Cap of 1 over namespace agent/a -> exactly 1 overflow, from agent/a only.
    over = store.select_facts_over_capacity(
        max_active_facts=1, source_store="agent/a"
    )
    assert len(over) == 1
    assert over[0]["source_store"] == "agent/a"
    assert over[0]["ext_key"] in {
        _ext_key_of(store, a1),
        _ext_key_of(store, a2),
    }


def test_default_source_store_constant_unchanged():
    # Guard: the default namespace constant is what the migration backfills.
    assert _DEFAULT_SOURCE_STORE == "orchestrator/self"
    # And the content-derived ext_key is deterministic (retention targets it).
    assert _content_ext_key("a b c") == _content_ext_key("a  b  c")
