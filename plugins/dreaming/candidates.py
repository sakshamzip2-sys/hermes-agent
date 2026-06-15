"""Candidate generation from v2 session history.

v1 dreamed over a pre-summarised ``episodic_events`` table. v2 has no such table —
conversation turns live raw in the ``messages`` table of ``$HERMES_HOME/state.db``
(see ``hermes_state.py``). So this adapter:

1. Reads recent user/assistant turns grouped by session (since the last run).
2. Builds a compact transcript *digest* per session.

The digest is later handed to an extraction LLM (``llm.extract_facts``) which
distils any durable, user-specific facts worth remembering. Those extracted facts
become the :class:`~plugins.dreaming.engine.DreamCandidate` objects the three-gate
engine scores.

This module also provides the **recall proxy**: v2 has no ``recall_citations``
table, so "how often did the user come back to this?" is approximated by an FTS5
count of how many *distinct sessions* mention a fact's salient terms — a faithful
stand-in for v1's social-proof signal.
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.plugins.dreaming.candidates")

_MAX_DIGEST_CHARS = 4000
_MAX_TURNS_PER_SESSION = 30
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "this", "that", "these",
    "those", "is", "are", "was", "were", "be", "been", "to", "of", "in", "on",
    "for", "with", "as", "at", "by", "it", "i", "you", "we", "they", "he", "she",
    "my", "your", "our", "me", "do", "does", "did", "can", "could", "would",
    "should", "have", "has", "had", "what", "how", "why", "when", "where", "so",
    "just", "like", "not", "no", "yes", "ok", "okay", "please", "thanks",
}


@dataclass(frozen=True)
class SessionDigest:
    session_id: str
    last_ts: float
    text: str

    @property
    def event_id(self) -> str:
        return hashlib.sha256(f"{self.session_id}:{self.last_ts}".encode()).hexdigest()[:16]


def _connect(db_path: Path) -> sqlite3.Connection:
    # Read-only; never mutate core's state.db.
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def build_session_digests(
    db_path: Path, *, since_ts: float = 0.0, limit: int = 50
) -> list[SessionDigest]:
    """Return up to *limit* session digests for sessions active after *since_ts*.

    Most-recently-active sessions first. Returns ``[]`` if the DB is missing or
    unreadable (dreaming degrades to a no-op rather than crashing a session).
    """
    if not db_path.exists():
        return []
    try:
        conn = _connect(db_path)
    except sqlite3.Error as exc:
        logger.warning("dreaming: cannot open state.db (%s); no candidates", exc)
        return []
    try:
        # Sessions with user/assistant activity after the cutoff, newest first.
        session_rows = conn.execute(
            """
            SELECT session_id, MAX(timestamp) AS last_ts
            FROM messages
            WHERE timestamp > ? AND role IN ('user', 'assistant')
              AND content IS NOT NULL AND length(trim(content)) > 0
            GROUP BY session_id
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()

        digests: list[SessionDigest] = []
        for srow in session_rows:
            sid = srow["session_id"]
            last_ts = float(srow["last_ts"] or 0.0)
            turns = conn.execute(
                """
                SELECT role, content FROM messages
                WHERE session_id = ? AND role IN ('user', 'assistant')
                  AND content IS NOT NULL AND length(trim(content)) > 0
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (sid, _MAX_TURNS_PER_SESSION),
            ).fetchall()
            text = _format_digest(turns)
            if text:
                digests.append(SessionDigest(session_id=sid, last_ts=last_ts, text=text))
        return digests
    except sqlite3.Error as exc:
        logger.warning("dreaming: state.db query failed (%s); no candidates", exc)
        return []
    finally:
        conn.close()


def _format_digest(turns: list) -> str:
    parts: list[str] = []
    for row in turns:
        role = row["role"]
        content = (row["content"] or "").strip()
        if not content:
            continue
        speaker = "User" if role == "user" else "Assistant"
        parts.append(f"{speaker}: {content}")
    digest = "\n".join(parts).strip()
    if len(digest) > _MAX_DIGEST_CHARS:
        digest = digest[:_MAX_DIGEST_CHARS] + " …[truncated]"
    return digest


def salient_terms(text: str, *, max_terms: int = 6) -> list[str]:
    """Pick the most salient non-stopword tokens from a fact for FTS matching."""
    # Allow 2-char tokens (Go, JS, ML, C++) — single-char language names are
    # rare enough to accept as a recall blind spot.
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+\-.]{1,}", text.lower())
    seen: dict[str, int] = {}
    for w in words:
        if w in _STOPWORDS:
            continue
        seen[w] = seen.get(w, 0) + 1
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:max_terms]]


def count_sessions_matching(db_path: Path, terms: list[str]) -> int:
    """FTS5 recall proxy: distinct sessions whose messages match *terms*.

    Returns the count of distinct sessions (the "how often did this resurface?"
    signal). Returns 0 on any failure or empty terms — the engine then treats
    recall as not-met for that fact.
    """
    if not terms or not db_path.exists():
        return 0
    # OR the salient terms; quote each to avoid FTS operator interpretation.
    query = " OR ".join(f'"{t}"' for t in terms)
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        return 0
    try:
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT m.session_id)
            FROM messages_fts f
            JOIN messages m ON m.id = f.rowid
            WHERE messages_fts MATCH ?
            """,
            (query,),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except sqlite3.Error as exc:
        logger.debug("dreaming: recall FTS query failed (%s); recall=0", exc)
        return 0
    finally:
        conn.close()


def default_state_db_path() -> Optional[Path]:
    """Resolve v2's state.db path, or None if core isn't importable."""
    try:
        from hermes_state import DEFAULT_DB_PATH

        return Path(DEFAULT_DB_PATH)
    except Exception:  # noqa: BLE001
        try:
            from hermes_constants import get_hermes_home

            return get_hermes_home() / "state.db"
        except Exception:  # noqa: BLE001
            return None
