"""STEP 9 — verify the no-key real memory layer (holographic) works cross-session.

The core `memory` tool is a flat MEMORY.md text store. A real memory layer with
fact storage + recall exists as the bundled, no-key `holographic` provider
(SQLite + FTS5 + HRR vectors + entity linking), enableable via
``memory.provider: holographic``. These tests prove the cross-session
store→retrieve and dedup/update behavior the upgrade plan asks for, so enabling
the provider is a verified upgrade (not a leap of faith).
"""

import pytest

from plugins.memory.holographic.store import MemoryStore


@pytest.fixture()
def db_path(tmp_path):
    return str(tmp_path / "memory_store.db")


def test_store_in_session_a_retrieve_in_session_b(db_path):
    """A fact written by one store instance is recalled by a fresh instance
    opening the same DB — the cross-session guarantee."""
    # Session A: store a preference, then close.
    store_a = MemoryStore(db_path=db_path)
    store_a.add_fact("The user prefers dark mode in the dashboard", category="preference")
    store_a.close()

    # Session B: a brand-new instance on the same DB recalls it.
    # FTS5 implicitly ANDs terms, so query with words present in the content.
    store_b = MemoryStore(db_path=db_path)
    results = store_b.search_facts("dark mode")
    store_b.close()

    assert results, "expected the stored preference to be recalled cross-session"
    assert any("dark mode" in r["content"].lower() for r in results)


def test_dedup_by_content(db_path):
    """Re-storing the same fact returns the same id (no duplicate rows)."""
    store = MemoryStore(db_path=db_path)
    id1 = store.add_fact("Postgres runs on port 5433 in staging", category="infra")
    id2 = store.add_fact("Postgres runs on port 5433 in staging", category="infra")
    store.close()
    assert id1 == id2


def test_fact_update_returns_latest(db_path):
    """When a fact is superseded, a later contradicting fact is retrievable —
    the temporal 'latest value' behavior (both are stored; recency/trust rank)."""
    store = MemoryStore(db_path=db_path)
    store.add_fact("The deploy region is us-east-1", category="infra")
    store.close()

    # Later session: the region changed.
    store2 = MemoryStore(db_path=db_path)
    store2.add_fact("The deploy region is now eu-west-1", category="infra")
    results = store2.search_facts("deploy region")
    store2.close()

    contents = " ".join(r["content"] for r in results)
    assert "eu-west-1" in contents  # the newer fact is retrievable


def test_provider_is_available_without_keys():
    """The holographic provider needs no API key — it's the no-cost upgrade path."""
    from plugins.memory.holographic import HolographicMemoryProvider
    provider = HolographicMemoryProvider()
    assert provider.name == "holographic"
    assert provider.is_available() is True


def test_provider_discovery_lists_holographic():
    """The provider is discoverable so `memory.provider: holographic` resolves."""
    from plugins.memory import discover_memory_providers
    providers = discover_memory_providers()
    names = [p[0] for p in providers]
    assert "holographic" in names
