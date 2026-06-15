"""Tests for candidate generation + FTS recall proxy against a temp state.db.

We build a minimal ``messages`` table + ``messages_fts`` mirror matching the
relevant columns of ``hermes_state.py`` so the adapter is exercised end to end.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from plugins.dreaming import candidates as candmod


def _make_state_db(path: Path, rows: list[tuple]) -> None:
    """rows: (id, session_id, role, content, timestamp)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, "
        "role TEXT, content TEXT, timestamp REAL)"
    )
    conn.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
    for r in rows:
        conn.execute(
            "INSERT INTO messages(id, session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            r,
        )
        conn.execute(
            "INSERT INTO messages_fts(rowid, content) VALUES (?, ?)", (r[0], r[3])
        )
    conn.commit()
    conn.close()


def test_build_digests_groups_by_session(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(
        db,
        [
            (1, "s1", "user", "I love hiking in the Alps", 100.0),
            (2, "s1", "assistant", "Great, the Alps are beautiful", 101.0),
            (3, "s2", "user", "My favorite language is Rust", 200.0),
            (4, "s2", "assistant", "Rust is memory-safe", 201.0),
        ],
    )
    digests = candmod.build_session_digests(db, since_ts=0.0, limit=50)
    assert len(digests) == 2
    # newest session first
    assert digests[0].session_id == "s2"
    assert "Rust" in digests[0].text
    assert "User:" in digests[0].text and "Assistant:" in digests[0].text


def test_build_digests_respects_since_ts(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(
        db,
        [
            (1, "old", "user", "ancient message", 10.0),
            (2, "new", "user", "fresh message", 500.0),
        ],
    )
    digests = candmod.build_session_digests(db, since_ts=100.0, limit=50)
    ids = {d.session_id for d in digests}
    assert ids == {"new"}


def test_build_digests_missing_db_returns_empty(tmp_path):
    assert candmod.build_session_digests(tmp_path / "nope.db") == []


def test_event_id_stable_and_distinct(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(db, [(1, "s1", "user", "hello world content", 100.0)])
    d1 = candmod.build_session_digests(db)[0]
    d2 = candmod.build_session_digests(db)[0]
    assert d1.event_id == d2.event_id  # stable
    assert len(d1.event_id) == 16


def test_salient_terms_filters_stopwords():
    terms = candmod.salient_terms("I really like the Rust programming language a lot")
    assert "rust" in terms
    assert "the" not in terms
    assert "i" not in terms


def test_recall_proxy_counts_distinct_sessions(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(
        db,
        [
            (1, "s1", "user", "rust is great", 100.0),
            (2, "s2", "user", "i am learning rust today", 200.0),
            (3, "s3", "user", "python is nice", 300.0),
        ],
    )
    # "rust" appears in s1 and s2 -> 2 distinct sessions
    assert candmod.count_sessions_matching(db, ["rust"]) == 2
    # "python" only in s3
    assert candmod.count_sessions_matching(db, ["python"]) == 1
    # nonsense -> 0
    assert candmod.count_sessions_matching(db, ["zzzznotawordzz"]) == 0


def test_recall_proxy_empty_terms(tmp_path):
    db = tmp_path / "state.db"
    _make_state_db(db, [(1, "s1", "user", "hello", 100.0)])
    assert candmod.count_sessions_matching(db, []) == 0
