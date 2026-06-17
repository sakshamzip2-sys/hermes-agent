"""review_mode: gate-passing promotions QUEUE for review instead of writing MEMORY.md."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from plugins.dreaming import llm, memory_io, review, runner
from plugins.dreaming.config import load_dreaming_config
from plugins.dreaming.store import DreamStore


def _make_state_db(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "role TEXT, content TEXT, timestamp REAL)"
    )
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    for r in rows:
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)", r
        )
        conn.execute("INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", (r[0], r[3]))
    conn.commit()
    conn.close()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: mem)
    db = tmp_path / "state.db"
    base = time.time() - 3600.0
    _make_state_db(db, [
        (1, "s1", "user", "I use Rust for all my backend services", base + 1),
        (2, "s2", "user", "Reminder: I still prefer Rust over Go", base + 3),
    ])
    return {"mem": mem, "db": db, "store": DreamStore(tmp_path / "dream.db"),
            "review_home": tmp_path / "dreaming"}


def _stub_llm(monkeypatch, facts):
    async def extract(_digest, max_facts=5):
        return list(facts)

    async def score_fn(_text):
        return 0.9

    monkeypatch.setattr(llm, "extract_facts", extract)
    monkeypatch.setattr(llm, "score_fact", score_fn)


def test_review_mode_queues_instead_of_writing_memory(env, monkeypatch):
    _stub_llm(monkeypatch, ["Uses Rust for backend services."])
    # Point the review queue at the temp home.
    monkeypatch.setattr(runner, "_review_home", lambda: env["review_home"])
    cfg = load_dreaming_config({"recall_gate_enabled": False, "review_mode": True})

    summary = asyncio.run(
        runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"])
    )

    # The fact passed all gates → reported promoted, but MEMORY.md was NOT written.
    assert summary.counts()["promoted"] == 1
    entries = memory_io.read_memory_entries()
    assert not any("Uses Rust" in e for e in entries), "review_mode must not write MEMORY.md"

    # Instead it sits in the HMAC-verified review queue.
    state = review.load_state(env["review_home"])
    assert len(state.items) == 1
    assert "Uses Rust" in state.items[0].text
    assert review.verify_chain(env["review_home"]) is True


def test_default_mode_still_writes_memory(env, monkeypatch):
    _stub_llm(monkeypatch, ["Uses Rust for backend services."])
    monkeypatch.setattr(runner, "_review_home", lambda: env["review_home"])
    cfg = load_dreaming_config({"recall_gate_enabled": False})  # review_mode defaults False

    asyncio.run(runner.run_dream_cycle(force=True, config=cfg, db_path=env["db"], store=env["store"]))
    entries = memory_io.read_memory_entries()
    assert any("Uses Rust" in e for e in entries)
    # Nothing queued for review.
    assert review.load_state(env["review_home"]).items == []
