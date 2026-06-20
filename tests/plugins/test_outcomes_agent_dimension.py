"""Tests for the agent/run-identity dimension on the outcomes store + engine.

Slice 0 of the Part 2 build plan: nullable agent_id/subagent_id/role columns on
turn_outcomes (added via the same additive guarded migration as trajectory), an
additive read seam to fetch scores by agent, and an optional agent_id threaded
through the engine. Every assertion here uses a TEMP db (tmp_path); the live
~/.hermes/dreaming/outcomes.db is never touched.
"""

from __future__ import annotations

import sqlite3

from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.store import OutcomesStore

# The legacy schema as it existed BEFORE the agent-identity columns (and even before
# trajectory, to prove the additive migration is robust to an old table shape).
_LEGACY_SCHEMA = """
CREATE TABLE turn_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    turn        TEXT NOT NULL,
    turn_score  REAL NOT NULL,
    composite   REAL,
    judge       REAL,
    ts          REAL NOT NULL
);
"""


def _columns(db_path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(turn_outcomes)")}
    finally:
        conn.close()


def _build_legacy_db(db_path) -> None:
    """Create a turn_outcomes table WITHOUT the new columns and seed one row."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO turn_outcomes (session_id, turn, turn_score, composite, judge, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-session", "t1", 0.42, 0.40, None, 1.0),
        )
        conn.commit()
    finally:
        conn.close()


# (a) migration adds the columns on a legacy DB and preserves existing rows ----------
def test_migration_adds_columns_and_preserves_rows(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _build_legacy_db(db)

    # Sanity: the legacy table genuinely lacks the new columns.
    before = _columns(db)
    assert "agent_id" not in before
    assert "subagent_id" not in before
    assert "role" not in before
    assert "trajectory" not in before

    # Opening the store runs the additive migration.
    store = OutcomesStore(db)

    after = _columns(db)
    assert {"agent_id", "subagent_id", "role", "trajectory"} <= after

    # The pre-existing legacy row survives untouched (no DROP/DELETE), with NULL
    # for every newly-added column.
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT session_id, turn, turn_score, agent_id, subagent_id, role, trajectory "
            "FROM turn_outcomes WHERE session_id = 'legacy-session'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "legacy-session"
    assert row[1] == "t1"
    assert abs(row[2] - 0.42) < 1e-9
    assert row[3] is None  # agent_id
    assert row[4] is None  # subagent_id
    assert row[5] is None  # role
    assert row[6] is None  # trajectory
    # And the legacy row is still readable through the public seam.
    assert store.recent_turn_scores() == [0.42]


# (b) the migration is idempotent (open twice, no error, no duplicate columns) -------
def test_migration_is_idempotent(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _build_legacy_db(db)

    OutcomesStore(db)  # first open: migrates
    cols_first = _columns(db)
    OutcomesStore(db)  # second open: must be a no-op, not raise
    cols_second = _columns(db)

    assert cols_first == cols_second
    # Exactly one of each new column (no accidental duplicate ADD COLUMN).
    assert sorted(cols_second).count("agent_id") == 1
    assert sorted(cols_second).count("subagent_id") == 1
    assert sorted(cols_second).count("role") == 1


def test_fresh_store_has_agent_columns(tmp_path) -> None:
    """A brand-new DB (no legacy table) also gets the columns."""
    db = tmp_path / "fresh.db"
    OutcomesStore(db)
    assert {"agent_id", "subagent_id", "role"} <= _columns(db)


# (c) two turns under different agent_ids read back grouped per agent ----------------
def test_records_grouped_by_agent(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "outcomes.db")
    s.record(session_id="S", turn=1, turn_score=0.20, ts=1.0, agent_id="atlas")
    s.record(session_id="S", turn=2, turn_score=0.90, ts=2.0, agent_id="forge")
    s.record(session_id="S", turn=3, turn_score=0.40, ts=3.0, agent_id="atlas")

    # recent_scores_by_agent returns only that agent's scores, newest-first.
    assert s.recent_scores_by_agent("atlas") == [0.40, 0.20]
    assert s.recent_scores_by_agent("forge") == [0.90]
    # Equivalent through the additive filter arg on recent_turn_scores.
    assert s.recent_turn_scores(agent_id="atlas") == [0.40, 0.20]
    assert s.recent_turn_scores(agent_id="forge") == [0.90]
    # Per-agent mean rollup (which agent produced good runs).
    by_agent = dict(s.recent_agent_scores())
    assert abs(by_agent["atlas"] - 0.30) < 1e-9  # mean(0.20, 0.40)
    assert abs(by_agent["forge"] - 0.90) < 1e-9
    # An unknown agent yields nothing.
    assert s.recent_scores_by_agent("nobody") == []


def test_subagent_and_role_persist(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "outcomes.db")
    s.record(
        session_id="S", turn=1, turn_score=0.5, ts=1.0,
        agent_id="atlas", subagent_id="sub-7", role="researcher",
    )
    conn = sqlite3.connect(str(tmp_path / "outcomes.db"))
    try:
        row = conn.execute(
            "SELECT agent_id, subagent_id, role FROM turn_outcomes"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("atlas", "sub-7", "researcher")


# (d) recording with agent_id=None still works (back-compat) -------------------------
def test_record_without_agent_id_is_back_compat(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "outcomes.db")
    # Exactly the legacy call shape — no identity kwargs.
    s.record(session_id="S", turn=1, turn_score=0.7, composite=0.6, judge=0.8, ts=1.0)
    assert s.recent_turn_scores() == [0.7]
    assert s.count() == 1
    # Identity-less rows are excluded from the per-agent rollup.
    assert s.recent_agent_scores() == []
    # And explicitly passing None behaves identically.
    s.record(session_id="S", turn=2, turn_score=0.3, ts=2.0,
             agent_id=None, subagent_id=None, role=None)
    assert s.recent_turn_scores() == [0.3, 0.7]
    assert s.recent_agent_scores() == []


# (e) the existing recent_turn_scores still returns the same shape -------------------
def test_recent_turn_scores_shape_unchanged(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "outcomes.db")
    s.record(session_id="A", turn=1, turn_score=0.10, ts=100.0)
    s.record(session_id="A", turn=2, turn_score=0.20, ts=200.0)
    s.record(session_id="A", turn=3, turn_score=0.30, ts=300.0, agent_id="x")
    # Default call: a flat list[float], newest-first, across ALL rows (agent or not).
    out = s.recent_turn_scores()
    assert out == [0.30, 0.20, 0.10]
    assert all(isinstance(v, float) for v in out)
    # recent_session_scores contract is also unchanged.
    by_session = dict(s.recent_session_scores())
    assert abs(by_session["A"] - 0.20) < 1e-9  # mean(0.10, 0.20, 0.30)


# engine threads agent_id end-to-end through both scoring paths ----------------------
def test_engine_finalize_threads_agent_id(tmp_path) -> None:
    store = OutcomesStore(tmp_path / "outcomes.db")
    engine = OutcomesEngine(store)
    engine.record_tool("S", success=True)
    engine.finalize_turn("S", 1, agent_id="atlas", subagent_id="sub-1", role="lead")

    conn = sqlite3.connect(str(tmp_path / "outcomes.db"))
    try:
        row = conn.execute(
            "SELECT agent_id, subagent_id, role FROM turn_outcomes"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("atlas", "sub-1", "lead")
    assert store.recent_scores_by_agent("atlas") and store.recent_scores_by_agent("forge") == []


def test_engine_staged_path_threads_agent_id(tmp_path) -> None:
    store = OutcomesStore(tmp_path / "outcomes.db")
    engine = OutcomesEngine(store)
    engine.record_tool("S", success=True)
    engine.stage_turn("S", "turn-1", trajectory_summary="did a thing", agent_id="forge")
    resolved = engine.resolve_pending("S", user_followup="thanks")
    assert resolved is not None

    conn = sqlite3.connect(str(tmp_path / "outcomes.db"))
    try:
        row = conn.execute("SELECT agent_id FROM turn_outcomes").fetchone()
    finally:
        conn.close()
    assert row == ("forge",)


def test_engine_finalize_without_agent_id_back_compat(tmp_path) -> None:
    store = OutcomesStore(tmp_path / "outcomes.db")
    engine = OutcomesEngine(store)
    engine.record_tool("S", success=True)
    # Legacy call shape, no identity kwargs.
    engine.finalize_turn("S", 1)
    conn = sqlite3.connect(str(tmp_path / "outcomes.db"))
    try:
        row = conn.execute("SELECT agent_id, subagent_id, role FROM turn_outcomes").fetchone()
    finally:
        conn.close()
    assert row == (None, None, None)
