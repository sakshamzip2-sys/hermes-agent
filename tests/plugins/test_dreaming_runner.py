"""End-to-end runner test: sessions in state.db -> promotions in MEMORY.md.

Drives the whole pipeline (candidates -> extract -> three gates -> promote/hold)
with LLM calls stubbed, against temp state.db + memories dir.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from plugins.dreaming import llm, memory_io, runner
from plugins.dreaming.config import load_dreaming_config
from plugins.dreaming.store import DreamStore


def _make_state_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "role TEXT, content TEXT, timestamp REAL)"
    )
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    _append_rows(conn, rows)
    conn.commit()
    conn.close()


def _append_rows(conn, rows: list[tuple]) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            r,
        )
        conn.execute("INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", (r[0], r[3]))


def _add_rows(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    _append_rows(conn, rows)
    conn.commit()
    conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import time

    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: mem)

    db = tmp_path / "state.db"
    # Timestamps must be recent — the runner's first-run lookback is 30 days.
    base = time.time() - 3600.0
    # "rust" recurs across two sessions -> recall proxy >= 2 for rust facts.
    _make_state_db(
        db,
        [
            (1, "s1", "user", "I use Rust for all my backend services", base + 1),
            (2, "s1", "assistant", "Rust is a solid choice", base + 2),
            (3, "s2", "user", "Reminder: I still prefer Rust over Go", base + 3),
            (4, "s2", "assistant", "Noted", base + 4),
        ],
    )
    store = DreamStore(tmp_path / "dream.db")
    return {"mem": mem, "db": db, "store": store}


def _stub_llm(monkeypatch, *, facts, score_value=0.9):
    async def extract(_digest, max_facts=5):
        return list(facts)

    async def score_fn(_text):
        return score_value

    monkeypatch.setattr(llm, "extract_facts", extract)
    monkeypatch.setattr(llm, "score_fact", score_fn)
    # lexical_embed is real (offline); decide_supersede only fires on dup.


def test_full_cycle_promotes_to_memory(env, monkeypatch):
    _stub_llm(monkeypatch, facts=["Uses Rust for backend services."])
    cfg = load_dreaming_config({"recall_gate_enabled": False})  # single corpus
    summary = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert summary.counts()["promoted"] == 1
    entries = memory_io.read_memory_entries()
    assert any("Uses Rust for backend services." in e for e in entries)


def test_recall_gate_holds_single_session_fact(env, monkeypatch):
    # A fact whose salient terms appear in only one session -> recall 1 -> held.
    _stub_llm(monkeypatch, facts=["Mentioned a unicornterm once."])
    cfg = load_dreaming_config({})  # recall gate ON (default)
    summary = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert summary.counts()["promoted"] == 0
    assert summary.counts()["held"] == 1


def test_recall_gate_promotes_recurring_fact(env, monkeypatch):
    # "rust" appears in s1 and s2 -> recall proxy 2 -> passes recall gate.
    _stub_llm(monkeypatch, facts=["Strongly prefers Rust."])
    cfg = load_dreaming_config({})
    summary = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert summary.counts()["promoted"] == 1


def test_idempotent_second_run_promotes_nothing(env, monkeypatch):
    _stub_llm(monkeypatch, facts=["Uses Rust for backend services."])
    cfg = load_dreaming_config({"recall_gate_enabled": False})
    asyncio.run(runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"]))
    # Reset last-run so the second pass RE-SCANS the same window; the
    # processed-ledger (not the time window) must now prevent re-promotion.
    env["store"].set_last_run_ts(0.0)
    summary2 = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert summary2.counts()["promoted"] == 0
    assert summary2.skipped_already_processed >= 1
    # MEMORY.md still has exactly one copy
    entries = memory_io.read_memory_entries()
    assert sum("Uses Rust for backend services." in e for e in entries) == 1


def test_debounce_skips_within_interval(env, monkeypatch):
    _stub_llm(monkeypatch, facts=["Uses Rust."])
    cfg = load_dreaming_config({"min_interval_hours": 6})
    # Prime last_run to "now" so a non-forced run is inside the debounce window.
    import time
    env["store"].set_last_run_ts(time.time())
    summary = asyncio.run(
        runner.run_dream_cycle(force=False, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert summary.counts()["evaluated"] == 0


def test_dreams_rescore_graduates_held_fact(env, monkeypatch):
    """A fact held at recall=1 must graduate once a later session lifts recall to 2."""
    import time

    _stub_llm(monkeypatch, facts=["Interested in quantum computing."])
    cfg = load_dreaming_config({})  # recall gate ON

    # Run 1: only one session mentions "quantum" -> recall 1 -> HELD in DREAMS.md.
    s1 = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert s1.counts()["held"] == 1
    assert s1.counts()["promoted"] == 0
    assert any("quantum" in f for f in memory_io.read_dreams_facts())
    assert not any("quantum" in e for e in memory_io.read_memory_entries())

    # Two new sessions mention "quantum", lifting the recall proxy to 2.
    base = time.time() - 60.0
    _add_rows(env["db"], [
        (10, "s3", "user", "back to quantum computing experiments today", base),
        (11, "s3", "assistant", "quantum noted", base + 1),
        (12, "s4", "user", "more quantum computing reading this evening", base + 2),
        (13, "s4", "assistant", "great, quantum again", base + 3),
    ])
    # Reset last-run so Pass 1 re-scores DREAMS.md against the new recall count.
    env["store"].set_last_run_ts(0.0)

    s2 = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )
    assert s2.counts()["promoted"] == 1
    assert any("quantum" in e for e in memory_io.read_memory_entries())
    # And it must have left the holding pen.
    assert not any("quantum" in f for f in memory_io.read_dreams_facts())


def test_empty_db_stamps_run(env, monkeypatch, tmp_path):
    _stub_llm(monkeypatch, facts=[])
    empty_db = tmp_path / "empty.db"
    _make_state_db(empty_db, [])
    cfg = load_dreaming_config({})
    summary = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=empty_db, store=env["store"])
    )
    assert summary.counts()["promoted"] == 0
    assert env["store"].last_run_ts() > 0
