"""The store must persist each turn's trajectory so the BATCH judge has context.

Found live: rejudge_recent passed an empty trajectory (never stored) → the real judge
returned None for every row → judge never populated.
"""

from __future__ import annotations

import asyncio

from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.store import OutcomesStore


def _engine(tmp_path, **kw):
    return OutcomesEngine(OutcomesStore(tmp_path / "o.db"), **kw)


def test_finalize_persists_trajectory(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.finalize_turn("s", 1, trajectory_summary="agent ran the build and it passed", now=1.0)
    rows = eng.store.recent_unjudged_rows()
    assert rows[0]["trajectory"] == "agent ran the build and it passed"


def test_staged_path_persists_trajectory(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.stage_turn("s", "t1", trajectory_summary="agent edited config.yaml")
    eng.flush_pending("s", now=1.0)
    assert eng.store.recent_unjudged_rows()[0]["trajectory"] == "agent edited config.yaml"


def test_rejudge_uses_the_persisted_trajectory(tmp_path) -> None:
    eng = _engine(tmp_path, judge_enabled=True)
    eng.finalize_turn("s", 1, trajectory_summary="agent fixed the failing test", now=1.0)

    seen = {}

    async def chat_fn(system, user, *, max_tokens):  # noqa: ANN001
        seen["user"] = user
        return "<judge_score>0.9</judge_score>"

    n = asyncio.run(eng.rejudge_recent(chat_fn=chat_fn))
    assert n == 1
    # The judge prompt actually contained the persisted trajectory.
    assert "agent fixed the failing test" in seen["user"]


def test_store_record_persists_and_reads_trajectory(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "o.db")
    s.record(session_id="A", turn="1", turn_score=0.5, ts=1.0, trajectory="did a thing")
    assert s.recent_unjudged_rows()[0]["trajectory"] == "did a thing"
