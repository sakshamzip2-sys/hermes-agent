"""SQLite store for per-turn outcome scores — the SENSE→DREAM data seam.

Owns the canonical ``turn_outcomes`` schema at ``$HERMES_HOME/dreaming/outcomes.db``.
Session-B (cross-engine plane) reads :func:`recent_turn_scores` /
:func:`recent_session_scores` to mark which sessions mattered; the dreaming runner
reads :func:`recent_turn_scores` to tune its promotion threshold. Keep this contract
stable (documented in DREAM_MEMORY_COORDINATION.md §2).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.plugins.outcomes.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turn_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    turn        INTEGER NOT NULL,
    turn_score  REAL NOT NULL,
    composite   REAL,
    judge       REAL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turn_outcomes_session ON turn_outcomes(session_id);
"""


class OutcomesStore:
    """Append-only ledger of per-turn quality scores."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        *,
        session_id: str,
        turn: int,
        turn_score: float,
        composite: Optional[float] = None,
        judge: Optional[float] = None,
        ts: float,
    ) -> None:
        """Append one turn's fused score (+ optional component scores)."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO turn_outcomes "
                "(session_id, turn, turn_score, composite, judge, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(session_id), int(turn), float(turn_score),
                 None if composite is None else float(composite),
                 None if judge is None else float(judge),
                 float(ts)),
            )
            conn.commit()
        finally:
            conn.close()

    def recent_turn_scores(self, limit: int = 150) -> list[float]:
        """Most-recent fused turn_scores, newest-first (by rowid)."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT turn_score FROM turn_outcomes "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
            return [float(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()

    def recent_session_scores(self, limit: int = 20) -> list[tuple[str, float]]:
        """Per-session mean turn_score for the most-recent sessions (newest-first).

        "Which sessions mattered" — Session-B reads this to bias cross-engine
        consolidation toward high-signal sessions.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT session_id, AVG(turn_score) AS m, MAX(id) AS last_id "
                "FROM turn_outcomes GROUP BY session_id "
                "ORDER BY last_id DESC LIMIT ?",
                (int(limit),),
            )
            return [(str(r[0]), float(r[1])) for r in cur.fetchall()]
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM turn_outcomes")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def recent_unjudged_rows(self, limit: int = 150) -> list[dict]:
        """Recent rows scored composite-only (judge IS NULL), newest-first.

        Used by the batch judge pass (``OutcomesEngine.rejudge_recent``).
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, session_id, turn, turn_score, composite "
                "FROM turn_outcomes WHERE judge IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def update_judged(self, row_id: int, *, judge: float, turn_score: float) -> None:
        """Fold a batch judge verdict into an existing row (id-addressed)."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE turn_outcomes SET judge = ?, turn_score = ? WHERE id = ?",
                (float(judge), float(turn_score), int(row_id)),
            )
            conn.commit()
        finally:
            conn.close()


def default_db_path() -> Path:
    """``$HERMES_HOME/dreaming/outcomes.db`` (co-located with the dreaming stores)."""
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "dreaming" / "outcomes.db"
    except Exception:  # noqa: BLE001 — standalone/test
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "dreaming" / "outcomes.db"


def recent_turn_scores(limit: int = 150, *, db_path: Path | str | None = None) -> list[float]:
    """Module-level seam: recent fused turn_scores without instantiating a store.

    Returns ``[]`` on any error (missing db, no table) so callers degrade gracefully.
    This is the function Session-B and the dreaming runner import.
    """
    try:
        path = Path(db_path) if db_path is not None else default_db_path()
        if not path.exists():
            return []
        return OutcomesStore(path).recent_turn_scores(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: recent_turn_scores read failed (%s)", exc)
        return []
