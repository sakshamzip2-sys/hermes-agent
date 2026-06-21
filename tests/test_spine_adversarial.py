"""Adversarial probes for the oc_runs event spine (db.py + events.py).

Hostile, edge, concurrency, and failure-path tests. Each probe asserts the
ROBUST/correct behavior so that a real bug makes the assertion FAIL. Stdlib +
pytest only, no network, no LLM. The spine DB is isolated to a tmp file and the
module's thread-local connection cache is reset between tests like the existing
spine tests.
"""

from __future__ import annotations

import threading

import pytest

from plugins.oc_runs import db, events


def _reset_local() -> None:
    for attr in ("conn", "path"):
        if hasattr(db._local, attr):
            try:
                if attr == "conn" and db._local.conn is not None:
                    db._local.conn.close()
            except Exception:
                pass
            delattr(db._local, attr)


@pytest.fixture()
def runs_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_local()
    yield
    _reset_local()


def _ev(run_id, etype=events.RUN_PROGRESS, *, dedupe_key=None, payload=None,
        parent_run_id=None, agent_id=None, team_id=None):
    return events.build_event(
        run_id,
        etype,
        source=events.SOURCE_AGENTS,
        parent_run_id=parent_run_id,
        agent_id=agent_id,
        team_id=team_id,
        payload=payload,
        dedupe_key=dedupe_key,
    )


# --------------------------------------------------------------------------- #
# Concurrency: many threads appending to the same run_id
# --------------------------------------------------------------------------- #

def test_concurrent_append_no_dedupe_strictly_monotonic_unique(runs_db):
    """N threads each append M keyless events to the SAME run_id. Every event
    must persist as a distinct row, seq must be strictly monotonic with no
    duplicates and no gaps relative to the count, and latest_seq must equal the
    total appended."""
    n_threads, per_thread = 12, 25
    total = n_threads * per_thread
    errors: list = []
    seqs: list = []
    lock = threading.Lock()

    def worker(tid):
        try:
            for i in range(per_thread):
                # Each worker thread gets its OWN thread-local connection.
                s = db.append_event(_ev("agents:hot", payload={"tid": tid, "i": i}))
                with lock:
                    seqs.append(s)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent appends raised: {errors[:3]}"
    assert len(seqs) == total
    # No duplicate seqs handed back.
    assert len(set(seqs)) == total, "duplicate seq returned to concurrent writers"
    # Every event physically persisted (no lost write / silent dedupe).
    _reset_local()
    persisted = db.events_for_run("agents:hot")
    assert len(persisted) == total, f"expected {total} rows, got {len(persisted)}"
    row_seqs = [e["seq"] for e in persisted]
    assert row_seqs == sorted(set(row_seqs)), "stored seqs not strictly increasing/unique"
    assert db.latest_seq() == max(row_seqs)


def test_concurrent_append_same_dedupe_key_collapses_to_one(runs_db):
    """Many threads racing to emit the SAME (run_id, dedupe_key) terminal must
    collapse to exactly one row, and every caller must receive the identical
    surviving seq (not 0, not a phantom)."""
    n_threads = 20
    returned: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(n_threads)

    def worker():
        try:
            barrier.wait()  # maximize the race
            s = db.append_event(
                _ev("agents:term", events.RUN_COMPLETED, dedupe_key="final")
            )
            with lock:
                returned.append(s)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"racing dedupe emits raised: {errors[:3]}"
    _reset_local()
    rows = db.events_for_run("agents:term")
    assert len(rows) == 1, f"dedupe_key did not collapse: {len(rows)} rows"
    surviving = rows[0]["seq"]
    # Every racing caller must observe the single surviving seq, never 0.
    assert 0 not in returned, "a deduped caller got seq 0 (failed to read existing row)"
    assert set(returned) == {surviving}, (
        f"callers disagreed on surviving seq: returned={set(returned)} survivor={surviving}"
    )


# --------------------------------------------------------------------------- #
# Idempotent re-emit (single thread)
# --------------------------------------------------------------------------- #

def test_reemit_same_dedupe_key_returns_same_seq(runs_db):
    s1 = db.append_event(_ev("agents:r", events.RUN_FAILED, dedupe_key="k1"))
    s2 = db.append_event(_ev("agents:r", events.RUN_FAILED, dedupe_key="k1"))
    s3 = db.append_event(_ev("agents:r", events.RUN_FAILED, dedupe_key="k1"))
    assert s1 == s2 == s3 and s1 > 0
    assert len(db.events_for_run("agents:r")) == 1


def test_two_run_ids_sharing_dedupe_key_both_insert(runs_db):
    """The UNIQUE index is (run_id, dedupe_key): the same dedupe_key under two
    distinct run_ids must NOT collide -- both insert with distinct seqs."""
    a = db.append_event(_ev("agents:A", events.RUN_COMPLETED, dedupe_key="shared"))
    b = db.append_event(_ev("agents:B", events.RUN_COMPLETED, dedupe_key="shared"))
    assert a != b and a > 0 and b > 0
    assert len(db.events_for_run("agents:A")) == 1
    assert len(db.events_for_run("agents:B")) == 1


def test_null_dedupe_keys_always_append(runs_db):
    """SQLite treats NULLs as distinct: many keyless events on one run all
    append (no accidental collapse)."""
    for _ in range(5):
        db.append_event(_ev("agents:null", events.HEARTBEAT))
    assert len(db.events_for_run("agents:null")) == 5


# --------------------------------------------------------------------------- #
# Payload integrity
# --------------------------------------------------------------------------- #

def test_large_payload_roundtrips(runs_db):
    big = "x" * (2 * 1024 * 1024)  # 2 MB string
    nested = {"blob": big, "list": list(range(1000))}
    s = db.append_event(_ev("agents:big", payload=nested))
    assert s > 0
    _reset_local()
    rows = db.events_for_run("agents:big")
    assert len(rows) == 1
    assert rows[0]["payload"]["blob"] == big
    assert rows[0]["payload"]["list"] == list(range(1000))


def test_unicode_and_nested_payload_roundtrips(runs_db):
    payload = {
        "emoji": "fire \U0001f525 snow ❄",
        "ja": "日本語テスト",
        "nested": {"a": [1, {"b": "éèê"}], "q": "he said \"hi\"\n\t"},
        "ctrl": "tab\tnewline\nbackslash\\end",  # control chars in JSON string
    }
    db.append_event(_ev("agents:uni", payload=payload))
    _reset_local()
    rows = db.events_for_run("agents:uni")
    assert len(rows) == 1, "unicode/nested payload was not stored"
    assert rows[0]["payload"] == payload, "payload did not round-trip byte-for-byte"


# --------------------------------------------------------------------------- #
# tail_since cursor edge cases
# --------------------------------------------------------------------------- #

def test_tail_since_negative_cursor_returns_all_oldest_first(runs_db):
    s1 = db.append_event(_ev("agents:t", events.RUN_CREATED))
    s2 = db.append_event(_ev("agents:t", events.RUN_PROGRESS))
    out = db.tail_since(-5)
    assert [e["seq"] for e in out] == [s1, s2]


def test_tail_since_cursor_beyond_latest_returns_empty(runs_db):
    db.append_event(_ev("agents:t", events.RUN_CREATED))
    latest = db.latest_seq()
    assert db.tail_since(latest) == []
    assert db.tail_since(latest + 1000) == []


def test_tail_since_limit_zero_returns_empty_not_all(runs_db):
    """A limit of 0 must return NO rows (LIMIT 0), never silently fall back to
    every row -- otherwise a paginating consumer would be flooded."""
    for _ in range(4):
        db.append_event(_ev("agents:t", events.RUN_PROGRESS))
    out = db.tail_since(0, limit=0)
    assert out == [], f"limit=0 returned {len(out)} rows instead of 0"


def test_tail_since_respects_positive_limit_and_order(runs_db):
    seqs = [db.append_event(_ev("agents:t", events.RUN_PROGRESS)) for _ in range(6)]
    out = db.tail_since(0, limit=3)
    assert [e["seq"] for e in out] == seqs[:3]


# --------------------------------------------------------------------------- #
# Missing optional fields
# --------------------------------------------------------------------------- #

def test_append_with_none_optional_fields(runs_db):
    ev = _ev("agents:opt", events.RUN_CREATED)
    assert ev["parent_run_id"] is None
    assert ev["agent_id"] is None
    assert ev["team_id"] is None
    s = db.append_event(ev)
    assert s > 0
    _reset_local()
    row = db.events_for_run("agents:opt")[0]
    assert row["parent_run_id"] is None
    assert row["agent_id"] is None
    assert row["team_id"] is None
    assert row["payload"] == {}


# --------------------------------------------------------------------------- #
# Empty-DB invariants
# --------------------------------------------------------------------------- #

def test_latest_seq_empty_db_is_zero(runs_db):
    assert db.latest_seq() == 0


def test_tail_since_empty_db_is_empty(runs_db):
    assert db.tail_since(0) == []
    assert db.tail_since(0, limit=10) == []


# --------------------------------------------------------------------------- #
# Durability: seq survives a connection drop (no reuse of dropped ids)
# --------------------------------------------------------------------------- #

def test_seq_strictly_increases_across_connection_drops(runs_db):
    s1 = db.append_event(_ev("agents:d", events.RUN_CREATED))
    _reset_local()  # simulate process/connection restart
    s2 = db.append_event(_ev("agents:d", events.RUN_PROGRESS))
    _reset_local()
    s3 = db.append_event(_ev("agents:d", events.RUN_COMPLETED, dedupe_key="end"))
    assert s1 < s2 < s3, "seq must keep increasing across connection drops (AUTOINCREMENT)"
