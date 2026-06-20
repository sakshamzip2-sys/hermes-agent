"""Tests for tamper-evident provenance on the holographic fact store.

These prove the web-validation BUILD-QUEUE #2 hardening added to
``plugins/memory/holographic/store.py``: additive, nullable ``content_hash`` +
``signature`` columns, a stable local signing key, signing on self-generated
writes, and a ``verify_fact`` that detects post-sign content tampering. The
point of the wave: a cross-fed row that forges ``source_tier="user_authored"``
CANNOT acquire a valid signature, so a downstream floor gate that checks
``verify_fact`` cannot be bypassed by a forgeable string tag.

Coverage:
  (a) the migration adds ``content_hash`` + ``signature`` to a LEGACY-shape DB,
      BACKFILLS ``content_hash`` for the legacy row, PRESERVES the row, and is
      IDEMPOTENT (re-open: no error, no duplicate column);
  (b) a self-generated fact (``orchestrator/self`` / ``orchestrator/shared``)
      gets a valid signature and ``verify_fact`` returns True;
  (c) directly UPDATE-ing a signed row's content makes ``verify_fact`` False
      (the live re-hash no longer matches the signed hash);
  (d) a legacy / unsigned row (signature IS NULL, e.g. a cross-fed namespace)
      verifies as unsigned -> ``verify_fact`` returns False, never crashes.

Every store builds in a temp dir with HERMES_HOME pointed at the same temp dir,
so the signing-key file is created under the temp home and nothing real is
touched.
"""

from __future__ import annotations

import sqlite3

import pytest

from plugins.memory.holographic import store as store_mod
from plugins.memory.holographic.store import (
    MemoryStore,
    _compute_content_hash,
    _content_ext_key,
)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Point HERMES_HOME at the temp dir and reset the cached signing key.

    The signing key is process-cached in ``store_mod._SIGNING_KEY``; reset it so
    each test resolves a fresh key under its own temp HERMES_HOME instead of
    leaking the developer's real key (or a key from a prior test).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(store_mod, "_SIGNING_KEY", None)
    yield


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "memory_store.db"))
    try:
        yield s
    finally:
        s.close()


def _facts_columns(conn: sqlite3.Connection) -> list[str]:
    return [row[1] for row in conn.execute("PRAGMA table_info(facts)").fetchall()]


def _make_legacy_db(path) -> None:
    """Create a pre-provenance ``facts`` table (no content_hash/signature) + a row."""
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


# ----------------------------------------------------------------------------
# (a) migration adds columns + backfills content_hash on a legacy DB, idempotent
# ----------------------------------------------------------------------------

def test_migration_adds_columns_and_backfills_legacy(tmp_path):
    db_path = tmp_path / "memory_store.db"
    _make_legacy_db(db_path)

    # Pre-migration: neither provenance column exists.
    raw = sqlite3.connect(str(db_path))
    pre_cols = _facts_columns(raw)
    raw.close()
    assert "content_hash" not in pre_cols
    assert "signature" not in pre_cols

    legacy_content = "The legacy capital fact says Paris"

    # Open via MemoryStore -> runs the migration.
    s = MemoryStore(db_path=str(db_path))
    try:
        cols = _facts_columns(s._conn)
        assert "content_hash" in cols
        assert "signature" in cols

        row = s._conn.execute(
            "SELECT content, content_hash, signature FROM facts WHERE content = ?",
            (legacy_content,),
        ).fetchone()
        # Legacy row preserved.
        assert row is not None
        assert row["content"] == legacy_content
        # content_hash backfilled = SHA-256 of the (normalized) content.
        assert row["content_hash"] == _compute_content_hash(legacy_content)
        # signature left NULL: a legacy row is UNSIGNED.
        assert row["signature"] is None
    finally:
        s.close()

    # Idempotent: re-open does not error or duplicate columns.
    s2 = MemoryStore(db_path=str(db_path))
    try:
        cols2 = _facts_columns(s2._conn)
        assert cols2.count("content_hash") == 1
        assert cols2.count("signature") == 1
        # Row still there, hash unchanged.
        row2 = s2._conn.execute(
            "SELECT content_hash FROM facts WHERE content = ?",
            (legacy_content,),
        ).fetchone()
        assert row2["content_hash"] == _compute_content_hash(legacy_content)
    finally:
        s2.close()


# ----------------------------------------------------------------------------
# (b) a self-generated fact gets a valid signature and verify_fact True
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("ns", ["orchestrator/self", "orchestrator/shared"])
def test_self_generated_fact_is_signed_and_verifies(store, ns):
    content = "The orchestrator says the API port is 8642"
    # source_store=None defaults to orchestrator/self; pass explicitly too.
    if ns == "orchestrator/self":
        store.add_fact(content)
    else:
        store.add_fact(content, source_store=ns)

    ext_key = _content_ext_key(content)
    row = store._conn.execute(
        "SELECT content_hash, signature, source_store FROM facts WHERE ext_key = ?",
        (ext_key,),
    ).fetchone()
    assert row is not None
    assert row["source_store"] == ns
    assert row["content_hash"] == _compute_content_hash(content)
    # Self-generated -> a non-null signature.
    assert row["signature"] is not None
    # And it verifies.
    assert store.verify_fact(ext_key) is True


def test_supersede_self_fact_is_signed_and_verifies(store):
    old_content = "The API port is 8000"
    new_content = "The API port is 8642"
    store.add_fact(old_content)
    old_key = _content_ext_key(old_content)

    new_key = store.supersede(old_key, new_content)
    # The new (superseding) self-fact is signed and verifies.
    assert store.verify_fact(new_key) is True


# ----------------------------------------------------------------------------
# (c) tampering with a row's content (direct UPDATE) -> verify_fact False
# ----------------------------------------------------------------------------

def test_content_tamper_breaks_verification(store):
    content = "The secret deploy token is alpha-original"
    store.add_fact(content)
    ext_key = _content_ext_key(content)
    # Sanity: verifies before tampering.
    assert store.verify_fact(ext_key) is True

    # Tamper: directly rewrite the content (leaving the signed hash stale),
    # exactly the cross-fed-mutation attack the signature is meant to catch.
    store._conn.execute(
        "UPDATE facts SET content = ? WHERE ext_key = ?",
        ("The secret deploy token is evil-injected", ext_key),
    )
    store._conn.commit()

    # The live re-hash no longer matches the signed hash -> verification fails.
    assert store.verify_fact(ext_key) is False


def test_signature_tamper_breaks_verification(store):
    """Flipping the signature itself (not the content) also fails to verify."""
    content = "A signed-then-resigned fact"
    store.add_fact(content)
    ext_key = _content_ext_key(content)
    assert store.verify_fact(ext_key) is True

    store._conn.execute(
        "UPDATE facts SET signature = ? WHERE ext_key = ?",
        ("deadbeef" * 8, ext_key),
    )
    store._conn.commit()
    assert store.verify_fact(ext_key) is False


# ----------------------------------------------------------------------------
# (d) a legacy / cross-fed unsigned row verifies as unsigned (no crash)
# ----------------------------------------------------------------------------

def test_unsigned_cross_fed_row_does_not_verify(store):
    """A cross-fed namespace is NOT signed; verify_fact returns False, no crash.

    This is the core security property: a row that forges
    metadata.source_tier="user_authored" but lives in a cross-fed namespace
    (here ``agent/honcho``) gets NO signature, so it cannot pass verify_fact and
    therefore cannot bypass a floor gate that requires a verified self-fact.
    """
    content = "A cross-fed claim pretending to be user-authored"
    store.add_fact(content, source_store="agent/honcho")
    ext_key = _content_ext_key(content)

    row = store._conn.execute(
        "SELECT content_hash, signature FROM facts WHERE ext_key = ?",
        (ext_key,),
    ).fetchone()
    assert row is not None
    # content_hash is still computed (tamper-evidence available)...
    assert row["content_hash"] == _compute_content_hash(content)
    # ...but the row is UNSIGNED.
    assert row["signature"] is None
    # verify_fact treats an unsigned row as not-a-verified-self-fact: False.
    assert store.verify_fact(ext_key) is False


def test_legacy_backfilled_row_verifies_as_unsigned(tmp_path):
    """A legacy row backfilled with content_hash (signature NULL) -> False, no crash."""
    db_path = tmp_path / "memory_store.db"
    _make_legacy_db(db_path)
    s = MemoryStore(db_path=str(db_path))
    try:
        ext_key = s._conn.execute(
            "SELECT ext_key FROM facts WHERE content = ?",
            ("The legacy capital fact says Paris",),
        ).fetchone()["ext_key"]
        # Unsigned legacy row: verifies False, never raises.
        assert s.verify_fact(ext_key) is False
    finally:
        s.close()


def test_verify_fact_unknown_key_returns_false(store):
    """An ext_key with no matching row returns False rather than raising."""
    assert store.verify_fact("no-such-ext-key") is False


# ----------------------------------------------------------------------------
# signing key resolution
# ----------------------------------------------------------------------------

def test_signing_key_reuses_review_hmac_key(tmp_path, monkeypatch):
    """When the dreaming review HMAC key exists, it is reused as the signing key."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(store_mod, "_SIGNING_KEY", None)

    review_key = b"\x11" * 32
    (tmp_path / ".review_hmac_key").write_text(review_key.hex(), encoding="utf-8")

    assert store_mod._resolve_signing_key() == review_key
    # No dedicated memory_signing_key file should have been created.
    assert not (tmp_path / "memory_signing_key").exists()


def test_signing_key_created_0600_when_no_review_key(tmp_path, monkeypatch):
    """Absent a review key, a dedicated memory_signing_key file is created 0600."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(store_mod, "_SIGNING_KEY", None)

    key = store_mod._resolve_signing_key()
    assert len(key) == 32
    key_file = tmp_path / "memory_signing_key"
    assert key_file.exists()
    # Stable: re-reading returns the same key, and the file holds its hex.
    assert bytes.fromhex(key_file.read_text(encoding="utf-8").strip()) == key

    import os
    import stat

    mode = stat.S_IMODE(os.stat(key_file).st_mode)
    assert mode == 0o600
