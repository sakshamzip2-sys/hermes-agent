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
    turn        TEXT NOT NULL,
    turn_score  REAL NOT NULL,
    composite   REAL,
    judge       REAL,
    trajectory  TEXT,
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
            # Migration: add columns to a pre-existing table that lacks them.
            # Every migration here is ADDITIVE (nullable ADD COLUMN), idempotent, and
            # never drops/deletes — default NULL preserves prior behavior. New rows opt
            # into the new columns; legacy rows keep NULL.
            cols = {r[1] for r in conn.execute("PRAGMA table_info(turn_outcomes)")}
            if "trajectory" not in cols:
                conn.execute("ALTER TABLE turn_outcomes ADD COLUMN trajectory TEXT")
            # Agent/run identity dimension: answer "which agent produced a good run".
            if "agent_id" not in cols:
                conn.execute("ALTER TABLE turn_outcomes ADD COLUMN agent_id TEXT")
            if "subagent_id" not in cols:
                conn.execute("ALTER TABLE turn_outcomes ADD COLUMN subagent_id TEXT")
            if "role" not in cols:
                conn.execute("ALTER TABLE turn_outcomes ADD COLUMN role TEXT")
            # Explicit user feedback signal in [0, 1] (Part 2, Slice 5). Additive
            # nullable column; NULL = no explicit feedback yet (the prior behavior).
            # Folded as a sample-count-weighted running mean by ``record_user_rating``.
            if "user_rating" not in cols:
                conn.execute("ALTER TABLE turn_outcomes ADD COLUMN user_rating REAL")
            if "rating_count" not in cols:
                conn.execute(
                    "ALTER TABLE turn_outcomes ADD COLUMN rating_count INTEGER NOT NULL DEFAULT 0"
                )
            conn.commit()
        finally:
            conn.close()

    def record(
        self,
        *,
        session_id: str,
        turn,  # str | int — the real hook turn_id is a non-numeric string  # noqa: ANN001
        turn_score: float,
        composite: Optional[float] = None,
        judge: Optional[float] = None,
        ts: float,
        trajectory: Optional[str] = None,
        agent_id: Optional[str] = None,
        subagent_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """Append one turn's fused score (+ optional component scores + trajectory).

        ``turn`` is stored as TEXT — real hook turn_ids look like
        ``20260617_164838_86bc83:...:bb2228c6``, not integers. ``trajectory`` is the turn
        summary the BATCH judge needs (without it the judge has nothing to score).

        ``agent_id``/``subagent_id``/``role`` carry the agent-identity dimension when a
        turn is produced under a known agent/run context. All default to None, which is
        stored as NULL and preserves the prior (identity-less) behavior exactly.
        """
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO turn_outcomes "
                "(session_id, turn, turn_score, composite, judge, trajectory, ts, "
                "agent_id, subagent_id, role) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(session_id), str(turn), float(turn_score),
                 None if composite is None else float(composite),
                 None if judge is None else float(judge),
                 None if trajectory is None else str(trajectory),
                 float(ts),
                 None if agent_id is None else str(agent_id),
                 None if subagent_id is None else str(subagent_id),
                 None if role is None else str(role)),
            )
            conn.commit()
        finally:
            conn.close()

    def recent_turn_scores(
        self, limit: int = 150, *, agent_id: Optional[str] = None
    ) -> list[float]:
        """Most-recent fused turn_scores, newest-first (by rowid).

        ``agent_id`` is an additive optional filter: when given, only rows recorded
        under that agent_id are returned. Default None preserves the existing
        contract (all rows), so existing callers are unaffected.
        """
        conn = self._connect()
        try:
            if agent_id is None:
                cur = conn.execute(
                    "SELECT turn_score FROM turn_outcomes "
                    "ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                )
            else:
                cur = conn.execute(
                    "SELECT turn_score FROM turn_outcomes "
                    "WHERE agent_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (str(agent_id), int(limit)),
                )
            return [float(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()

    def recent_scores_by_agent(
        self, agent_id: str, limit: int = 150
    ) -> list[float]:
        """Most-recent fused turn_scores for a single ``agent_id``, newest-first.

        A convenience read seam for the agent-identity dimension. Equivalent to
        ``recent_turn_scores(limit, agent_id=agent_id)``; kept as a named method so
        the intent ("scores for this agent") reads clearly at call sites.
        """
        return self.recent_turn_scores(limit, agent_id=agent_id)

    def recent_agent_scores(self, limit: int = 20) -> list[tuple[str, float]]:
        """Per-agent mean turn_score for the most-recent agents (newest-first).

        Answers "which agent produced good runs". Mirrors
        :meth:`recent_session_scores` but groups by ``agent_id``; rows with a NULL
        agent_id (the identity-less default) are excluded.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT agent_id, AVG(turn_score) AS m, MAX(id) AS last_id "
                "FROM turn_outcomes WHERE agent_id IS NOT NULL "
                "GROUP BY agent_id "
                "ORDER BY last_id DESC LIMIT ?",
                (int(limit),),
            )
            return [(str(r[0]), float(r[1])) for r in cur.fetchall()]
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

    def session_turn_rows(self, session_id: str, *, limit: int = 150) -> list[dict]:
        """All scored rows for one ``session_id`` (newest-first), read-only.

        The Part 2 score bridge reads these to push each turn's fused ``turn_score``
        onto the matching Langfuse trace. Returns ``id``, ``turn``, ``turn_score``,
        ``composite``, ``judge``, ``user_rating`` so the bridge can attach the verdict
        without re-scoring. Never mutates the ledger.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, turn, turn_score, composite, judge, user_rating "
                "FROM turn_outcomes WHERE session_id = ? "
                "ORDER BY id DESC LIMIT ?",
                (str(session_id), int(limit)),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def count(self) -> int:
        conn = self._connect()
        try:
            cur = conn.execute("SELECT COUNT(*) FROM turn_outcomes")
            return int(cur.fetchone()[0])
        finally:
            conn.close()

    def recent_low_scoring_rows(
        self, *, score_below: float = 0.5, limit: int = 50
    ) -> list[dict]:
        """Recent rows whose fused turn_score is below ``score_below``, newest-first.

        The reflection PROPOSAL pass reads these to find what failed (low-quality turns)
        and which agent produced them. Returns full detail (session_id, agent_id, role,
        turn_score, trajectory) so the pass can cluster patterns. Read-only; never mutates.
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, session_id, turn, turn_score, composite, judge, trajectory, "
                "agent_id, subagent_id, role, user_rating, ts "
                "FROM turn_outcomes WHERE turn_score < ? "
                "ORDER BY id DESC LIMIT ?",
                (float(score_below), int(limit)),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def recent_unjudged_rows(self, limit: int = 150) -> list[dict]:
        """Recent rows scored composite-only (judge IS NULL), newest-first.

        Used by the batch judge pass (``OutcomesEngine.rejudge_recent``).
        """
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, session_id, turn, turn_score, composite, trajectory "
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

    def record_user_rating(self, *, session_id: str, turn, signal: float) -> bool:  # noqa: ANN001
        """Fold one explicit user-feedback ``signal`` in [0, 1] into a turn's running mean.

        Updates the matching ``turn_outcomes`` row's ``user_rating`` (a sample-count-
        weighted running mean over ``rating_count`` prior signals) without touching
        ``turn_score`` or any scorer-owned column. Additive and PRAGMA-guarded: if the
        ``user_rating`` column is absent (an old DB this build never migrated), this is a
        safe no-op. Returns True when a row was updated, False otherwise (no such turn, or
        column missing). Never deletes; never trains a model.
        """
        conn = self._connect()
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(turn_outcomes)")}
            if "user_rating" not in cols or "rating_count" not in cols:
                return False
            cur = conn.execute(
                "SELECT id, user_rating, rating_count FROM turn_outcomes "
                "WHERE session_id = ? AND turn = ? ORDER BY id DESC LIMIT 1",
                (str(session_id), str(turn)),
            )
            row = cur.fetchone()
            if row is None:
                return False
            row_id, prev_mean, prev_count = row[0], row[1], row[2]
            try:
                prev_count = int(prev_count or 0)
            except (TypeError, ValueError):
                prev_count = 0
            if prev_mean is None or prev_count <= 0:
                new_mean = float(signal)
            else:
                new_mean = (float(prev_mean) * prev_count + float(signal)) / (prev_count + 1)
            conn.execute(
                "UPDATE turn_outcomes SET user_rating = ?, rating_count = ? WHERE id = ?",
                (new_mean, prev_count + 1, int(row_id)),
            )
            conn.commit()
            return True
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


def recent_low_scoring_rows(
    *, score_below: float = 0.5, limit: int = 50, db_path: Path | str | None = None
) -> list[dict]:
    """Module-level seam: recent low-scoring rows without instantiating a store.

    Returns ``[]`` on any error (missing db, no table) so callers degrade gracefully.
    This is the function the reflection PROPOSAL pass imports.
    """
    try:
        path = Path(db_path) if db_path is not None else default_db_path()
        if not path.exists():
            return []
        return OutcomesStore(path).recent_low_scoring_rows(
            score_below=score_below, limit=limit
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: recent_low_scoring_rows read failed (%s)", exc)
        return []


def session_turn_rows(
    session_id: str, *, limit: int = 150, db_path: Path | str | None = None
) -> list[dict]:
    """Module-level seam: a session's scored rows without instantiating a store.

    Returns ``[]`` on any error (missing db, no table) so callers degrade gracefully.
    This is the function the Part 2 Langfuse score bridge imports.
    """
    try:
        path = Path(db_path) if db_path is not None else default_db_path()
        if not path.exists():
            return []
        return OutcomesStore(path).session_turn_rows(session_id, limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.debug("outcomes: session_turn_rows read failed (%s)", exc)
        return []
