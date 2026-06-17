"""Regression: a non-numeric turn_id (the REAL hook value) must record, not crash.

Found in a live run: real turn_ids look like '20260617_164838_86bc83:...:bb2228c6';
the engine did int(turn) and the whole on_session_end glue threw → nothing recorded.
"""

from __future__ import annotations

from plugins.outcomes.engine import OutcomesEngine
from plugins.outcomes.store import OutcomesStore


def _engine(tmp_path):
    return OutcomesEngine(OutcomesStore(tmp_path / "o.db"))


def test_string_turn_id_records(tmp_path) -> None:
    eng = _engine(tmp_path)
    tid = "20260617_164838_86bc83:20260617_164838_86bc83:bb2228c6"
    score = eng.finalize_turn("sess-1", tid, now=1.0)
    assert score == 0.5
    assert eng.store.count() == 1


def test_staged_path_with_string_turn_id(tmp_path) -> None:
    eng = _engine(tmp_path)
    eng.record_tool("sess-1", success=True)
    eng.stage_turn("sess-1", "turn-abc:def")
    score = eng.flush_pending("sess-1", now=1.0)
    assert score is not None and score > 0.5
    assert eng.store.count() == 1


def test_store_record_accepts_string_turn(tmp_path) -> None:
    s = OutcomesStore(tmp_path / "o.db")
    s.record(session_id="A", turn="weird:turn:id", turn_score=0.7, ts=1.0)
    assert s.recent_turn_scores() == [0.7]


def test_numeric_turn_still_works(tmp_path) -> None:
    eng = _engine(tmp_path)
    assert eng.finalize_turn("sess-1", 1, now=1.0) == 0.5
    assert eng.store.count() == 1
