"""Tests for the outcomes SQLite store (turn_outcomes schema + read seams)."""

from __future__ import annotations

from plugins.outcomes.store import OutcomesStore


def _store(tmp_path):
    return OutcomesStore(tmp_path / "outcomes.db")


def test_empty_store_reads_empty(tmp_path) -> None:
    s = _store(tmp_path)
    assert s.recent_turn_scores() == []
    assert s.recent_session_scores() == []
    assert s.count() == 0


def test_record_and_recent_is_newest_first(tmp_path) -> None:
    s = _store(tmp_path)
    s.record(session_id="A", turn=1, turn_score=0.10, ts=100.0)
    s.record(session_id="A", turn=2, turn_score=0.20, ts=200.0)
    s.record(session_id="A", turn=3, turn_score=0.30, ts=300.0)
    assert s.recent_turn_scores() == [0.30, 0.20, 0.10]
    assert s.count() == 3


def test_recent_respects_limit(tmp_path) -> None:
    s = _store(tmp_path)
    for i in range(10):
        s.record(session_id="A", turn=i, turn_score=i / 10.0, ts=float(i))
    assert s.recent_turn_scores(limit=3) == [0.9, 0.8, 0.7]


def test_persists_across_instances(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    OutcomesStore(db).record(session_id="A", turn=1, turn_score=0.5, ts=1.0)
    # A fresh instance against the same file sees the row.
    assert OutcomesStore(db).recent_turn_scores() == [0.5]


def test_recent_session_scores_aggregates_mean(tmp_path) -> None:
    s = _store(tmp_path)
    s.record(session_id="A", turn=1, turn_score=0.4, ts=1.0)
    s.record(session_id="A", turn=2, turn_score=0.6, ts=2.0)
    s.record(session_id="B", turn=1, turn_score=0.9, ts=3.0)
    by_session = dict(s.recent_session_scores())
    assert abs(by_session["A"] - 0.5) < 1e-9  # mean(0.4, 0.6)
    assert abs(by_session["B"] - 0.9) < 1e-9


def test_record_stores_composite_and_judge_components(tmp_path) -> None:
    s = _store(tmp_path)
    s.record(session_id="A", turn=1, turn_score=0.7, composite=0.5, judge=0.9, ts=1.0)
    # The fused score is what recent_turn_scores returns.
    assert s.recent_turn_scores() == [0.7]
    assert s.count() == 1
