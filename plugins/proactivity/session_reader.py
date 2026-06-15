"""Read recent conversation history from v2's state.db (read-only).

Shared by the conversation-native sources (commitment, inactivity, follow-up). Opens
``$HERMES_HOME/state.db`` in read-only mode and never mutates core's data.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.plugins.proactivity.session_reader")

_MAX_MSG_CHARS = 1500


@dataclass(frozen=True)
class Turn:
    role: str
    content: str
    timestamp: float


def default_state_db() -> Optional[Path]:
    try:
        from hermes_state import DEFAULT_DB_PATH

        return Path(DEFAULT_DB_PATH)
    except Exception:  # noqa: BLE001
        try:
            from hermes_constants import get_hermes_home

            return get_hermes_home() / "state.db"
        except Exception:  # noqa: BLE001
            return None


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def recent_user_messages(db_path: Path, *, since_ts: float = 0.0, limit: int = 50) -> list[Turn]:
    """Recent user turns (oldest-first) after *since_ts*. Empty on any failure."""
    if not db_path or not db_path.exists():
        return []
    try:
        conn = _connect(db_path)
    except sqlite3.Error as exc:
        logger.debug("proactivity: cannot open state.db (%s)", exc)
        return []
    try:
        rows = conn.execute(
            """
            SELECT role, content, timestamp FROM messages
            WHERE role = 'user' AND timestamp > ?
              AND content IS NOT NULL AND length(trim(content)) > 0
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            (since_ts, limit),
        ).fetchall()
        out: list[Turn] = []
        for r in rows:
            content = (r["content"] or "").strip()
            if len(content) > _MAX_MSG_CHARS:
                content = content[:_MAX_MSG_CHARS] + " …"
            out.append(Turn(role=r["role"], content=content, timestamp=float(r["timestamp"] or 0.0)))
        return out
    except sqlite3.Error as exc:
        logger.debug("proactivity: state.db query failed (%s)", exc)
        return []
    finally:
        conn.close()


def last_user_message_ts(db_path: Path) -> Optional[float]:
    """Epoch-seconds of the most recent user message, or None."""
    if not db_path or not db_path.exists():
        return None
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT MAX(timestamp) FROM messages WHERE role = 'user'"
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()
