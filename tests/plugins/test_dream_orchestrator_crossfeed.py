"""Phase 2 — one-way cross-feed + the no-recursion HARD INVARIANT.

Proves:
* imported lines are namespaced + provenance-tagged,
* the confidence floor + max_imports cap are honoured,
* dry_run previews without writing,
* live runs write through the local diversity gate + the import ledger, and
* the HARD INVARIANT: a derived/imported line is EXCLUDED from the LOCAL dreamer's
  candidate pool on subsequent runs (no recursion -> no model collapse).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from plugins.dream_orchestrator import importer
from plugins.dream_orchestrator.config import CrossFeedConfig
from plugins.dream_orchestrator.importer import ImportCandidate, is_derived_line, run_cross_feed
from plugins.dream_orchestrator.store import OrchestratorStore


# ---------------------------------------------------------------------------
# Provenance marker / derived-line recognition
# ---------------------------------------------------------------------------
def test_provenance_line_is_namespaced_and_tagged():
    c = ImportCandidate("honcho", "abc123", "The user ships on Fridays", "high")
    line = c.provenance_line()
    assert "honcho#abc123" in line
    assert "conf=high" in line
    assert line.endswith("The user ships on Fridays")
    assert is_derived_line(line)


def test_bare_text_is_not_a_derived_line():
    assert not is_derived_line("The user ships on Fridays")
    assert not is_derived_line("(dreamed 2026-06-17) a normal local promotion")


# ---------------------------------------------------------------------------
# HARD INVARIANT: derived lines excluded from the LOCAL dreamer's candidates
# ---------------------------------------------------------------------------
def test_local_dreamer_recognizes_derived_facts():
    from plugins.dreaming import candidates as candmod

    derived = ImportCandidate("gbrain", "f9", "Prefers TypeScript", "high").provenance_line()
    assert candmod.is_derived_fact(derived) is True
    assert candmod.is_derived_fact("Prefers TypeScript") is False


def _make_state_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "role TEXT, content TEXT, timestamp REAL)"
    )
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    for r in rows:
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)", r,
        )
        conn.execute("INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", (r[0], r[3]))
    conn.commit()
    conn.close()


def test_imported_line_is_not_re_dreamed(tmp_path, monkeypatch):
    """A derived line that surfaces in a session transcript must NOT be re-promoted."""
    import time

    from plugins.dreaming import llm, memory_io, runner
    from plugins.dreaming.config import load_dreaming_config
    from plugins.dreaming.store import DreamStore

    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: mem)

    derived = ImportCandidate("honcho", "c1", "User loves Rust", "high").provenance_line()

    # The extractor returns the DERIVED line verbatim (as if the transcript echoed
    # an imported memory). The runner must drop it before scoring.
    async def extract(_digest, max_facts=5):
        return [derived]

    async def score_fn(_text):
        return 0.99

    monkeypatch.setattr(llm, "extract_facts", extract)
    monkeypatch.setattr(llm, "score_fact", score_fn)

    db = tmp_path / "state.db"
    base = time.time() - 3600.0
    _make_state_db(db, [(1, "s1", "user", "talking about " + derived, base + 1)])

    cfg = load_dreaming_config({"recall_gate_enabled": False})
    store = DreamStore(tmp_path / "dream.db")
    summary = asyncio.run(runner.run_dream_cycle(force=True, config=cfg, db_path=db, store=store))

    # The derived line was excluded -> nothing promoted, nothing held.
    assert summary.counts()["promoted"] == 0
    assert summary.counts()["evaluated"] == 0
    assert memory_io.read_memory_entries() == []


# ---------------------------------------------------------------------------
# run_cross_feed: floor, cap, dry-run, live promotion
# ---------------------------------------------------------------------------
@pytest.fixture()
def crossfeed_env(tmp_path, monkeypatch):
    from plugins.dreaming import memory_io

    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: mem)
    store = OrchestratorStore(tmp_path / "orch.db")
    # No live diversity embeddings in tests -> everything is "novel".
    monkeypatch.setattr(importer, "_local_diversity", lambda: (0.8, None))
    return {"mem": mem, "store": store, "memory_io": memory_io}


def _stub_sources(monkeypatch, honcho=None, gbrain=None):
    monkeypatch.setattr(importer, "fetch_honcho_conclusions", lambda limit=50: list(honcho or []))
    monkeypatch.setattr(importer, "fetch_gbrain_facts", lambda limit=50: list(gbrain or []))


def test_cross_feed_dry_run_previews_without_writing(crossfeed_env, monkeypatch):
    _stub_sources(monkeypatch, honcho=[ImportCandidate("honcho", "c1", "Fact A", "high")])
    cf = CrossFeedConfig(enabled=True, dry_run=True, confidence_floor="high",
                         max_imports_per_run=20)
    summary = run_cross_feed(cf, crossfeed_env["store"])
    assert len(summary.previewed) == 1
    assert summary.promoted == []
    assert crossfeed_env["memory_io"].read_memory_entries() == []
    # Dry run does not ledger.
    assert crossfeed_env["store"].imported_ids() == set()


def test_cross_feed_live_promotes_and_ledgers(crossfeed_env, monkeypatch):
    _stub_sources(monkeypatch, honcho=[ImportCandidate("honcho", "c1", "Fact A", "high")],
                  gbrain=[ImportCandidate("gbrain", "f1", "Fact B", "high")])
    cf = CrossFeedConfig(enabled=True, dry_run=False, confidence_floor="high",
                         max_imports_per_run=20)
    summary = run_cross_feed(cf, crossfeed_env["store"])
    assert len(summary.promoted) == 2
    entries = crossfeed_env["memory_io"].read_memory_entries()
    assert any("honcho#c1" in e and "Fact A" in e for e in entries)
    assert any("gbrain#f1" in e and "Fact B" in e for e in entries)
    # Ledgered -> a second run imports nothing new.
    again = run_cross_feed(cf, crossfeed_env["store"])
    assert again.promoted == []
    assert again.skipped_existing == 2


def test_cross_feed_confidence_floor_excludes_low(crossfeed_env, monkeypatch):
    _stub_sources(monkeypatch, gbrain=[
        ImportCandidate("gbrain", "f1", "low conf fact", "low"),
        ImportCandidate("gbrain", "f2", "high conf fact", "high"),
    ])
    cf = CrossFeedConfig(enabled=True, dry_run=True, confidence_floor="high",
                         max_imports_per_run=20)
    summary = run_cross_feed(cf, crossfeed_env["store"])
    assert len(summary.previewed) == 1
    assert "high conf fact" in summary.previewed[0]


def test_cross_feed_caps_imports_per_run(crossfeed_env, monkeypatch):
    many = [ImportCandidate("honcho", f"c{i}", f"Fact {i}", "high") for i in range(10)]
    _stub_sources(monkeypatch, honcho=many)
    cf = CrossFeedConfig(enabled=True, dry_run=True, confidence_floor="high",
                         max_imports_per_run=3)
    summary = run_cross_feed(cf, crossfeed_env["store"])
    assert len(summary.previewed) == 3
