"""Tests for the dreaming SQLite state store."""

from __future__ import annotations

from plugins.dreaming.store import DreamStore


def test_processed_ledger_roundtrip(tmp_path):
    st = DreamStore(tmp_path / "d.db")
    assert st.processed_ids() == set()
    st.mark_processed(["a", "b", "c"])
    assert st.processed_ids() == {"a", "b", "c"}
    # idempotent re-insert
    st.mark_processed(["a", "d"])
    assert st.processed_ids() == {"a", "b", "c", "d"}


def test_mark_processed_empty_noop(tmp_path):
    st = DreamStore(tmp_path / "d.db")
    st.mark_processed([])
    assert st.processed_ids() == set()


def test_last_run_ts(tmp_path):
    st = DreamStore(tmp_path / "d.db")
    assert st.last_run_ts() == 0.0
    st.set_last_run_ts(123456.5)
    assert st.last_run_ts() == 123456.5


def test_meta_roundtrip(tmp_path):
    st = DreamStore(tmp_path / "d.db")
    assert st.get_meta("missing") is None
    st.set_meta("k", "v1")
    assert st.get_meta("k") == "v1"
    st.set_meta("k", "v2")  # upsert
    assert st.get_meta("k") == "v2"


def test_audit_records_runs(tmp_path):
    st = DreamStore(tmp_path / "d.db")
    st.record_run({"promoted": 2, "held": 1})
    st.record_run({"promoted": 0, "held": 3})
    runs = st.recent_runs(limit=10)
    assert len(runs) == 2
    # newest first
    assert runs[0]["promoted"] == 0
    assert runs[1]["promoted"] == 2
    assert "ts" in runs[0]


def test_persists_across_instances(tmp_path):
    db = tmp_path / "d.db"
    DreamStore(db).mark_processed(["x"])
    assert DreamStore(db).processed_ids() == {"x"}
