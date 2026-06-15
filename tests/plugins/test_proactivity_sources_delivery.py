"""Tests for the commitment source, inactivity source, and gateway delivery."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from plugins.proactivity import gateway_delivery, llm
from plugins.proactivity.moment import Category
from plugins.proactivity.sources.base import PollContext
from plugins.proactivity.sources.commitment import CommitmentSource
from plugins.proactivity.sources.inactivity import InactivitySource


def _run(coro):
    return asyncio.run(coro)


def _state_db(path: Path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL)")
    for i, (role, content, ts) in enumerate(rows, start=1):
        conn.execute("INSERT INTO messages(id, role, content, timestamp) VALUES (?,?,?,?)",
                     (i, role, content, ts))
    conn.commit()
    conn.close()


# -- commitment source ------------------------------------------------------

def test_commitment_source_emits_moments(tmp_path, monkeypatch):
    now = 1_000_000.0
    db = tmp_path / "state.db"
    _state_db(db, [
        ("user", "I'll email Sam about the deck on Friday", now - 100),
        ("user", "what's the weather", now - 50),  # no commitment hint
    ])
    monkeypatch.setattr(llm, "aux_available", lambda: True)

    async def fake_extract(digest, max_items=5):
        assert "email Sam" in digest  # only hinted messages reach the LLM
        return [{"what": "Email Sam about the deck", "due": "Friday", "asked_reminder": False}]

    monkeypatch.setattr(llm, "extract_commitments", fake_extract)

    src = CommitmentSource()
    ctx = PollContext(now=now, home=tmp_path, state_db=db)
    moments = _run(src.poll(ctx))
    assert len(moments) == 1
    m = moments[0]
    assert m.category is Category.COMMITMENT
    assert "email sam" in m.body.lower()
    assert m.push_eligible  # TOLD_FACT commitment is push-eligible


def test_commitment_asked_reminder_is_user_loop(tmp_path, monkeypatch):
    now = 1_000_000.0
    db = tmp_path / "state.db"
    _state_db(db, [("user", "remind me to call the bank tomorrow", now - 100)])
    monkeypatch.setattr(llm, "aux_available", lambda: True)

    async def fake_extract(digest, max_items=5):
        return [{"what": "Call the bank", "due": "tomorrow", "asked_reminder": True}]

    monkeypatch.setattr(llm, "extract_commitments", fake_extract)
    moments = _run(CommitmentSource().poll(PollContext(now=now, home=tmp_path, state_db=db)))
    assert moments[0].sensitivity.value == "user_loop"


def test_commitment_source_unavailable_without_aux(monkeypatch):
    monkeypatch.setattr(llm, "aux_available", lambda: False)
    assert CommitmentSource().available() is False


def test_commitment_no_hints_no_llm_call(tmp_path, monkeypatch):
    now = 1_000_000.0
    db = tmp_path / "state.db"
    _state_db(db, [("user", "just saying hello", now - 100)])
    monkeypatch.setattr(llm, "aux_available", lambda: True)

    called = []

    async def fake_extract(digest, max_items=5):
        called.append(1)
        return []

    monkeypatch.setattr(llm, "extract_commitments", fake_extract)
    moments = _run(CommitmentSource().poll(PollContext(now=now, home=tmp_path, state_db=db)))
    assert moments == []
    assert called == []  # no commitment hint -> no paid LLM call


# -- inactivity source ------------------------------------------------------

def test_inactivity_emits_after_quiet_period(tmp_path):
    now = 1_000_000.0
    db = tmp_path / "state.db"
    _state_db(db, [("user", "hi", now - 10 * 86400.0)])  # 10 days ago
    src = InactivitySource(quiet_days=7.0)
    moments = _run(src.poll(PollContext(now=now, home=tmp_path, state_db=db)))
    assert len(moments) == 1
    assert moments[0].category is Category.RE_ENGAGEMENT
    assert not moments[0].push_eligible  # re-engagement never pushes


def test_inactivity_silent_when_recent(tmp_path):
    now = 1_000_000.0
    db = tmp_path / "state.db"
    _state_db(db, [("user", "hi", now - 3600.0)])  # 1h ago
    moments = _run(InactivitySource(quiet_days=7.0).poll(PollContext(now=now, home=tmp_path, state_db=db)))
    assert moments == []


# -- gateway delivery -------------------------------------------------------

def test_gateway_delivery_uses_injected_fn():
    captured = {}

    def fake(job, content, **kw):
        captured["job"] = job
        captured["content"] = content
        return None  # cron convention: None = success

    gateway_delivery.set_deliver_fn(fake)
    try:
        assert gateway_delivery.deliver("hello there") is True
        assert captured["content"] == "hello there"
        assert captured["job"]["deliver"] == "all"
    finally:
        gateway_delivery.set_deliver_fn(None)


def test_gateway_delivery_error_returns_false():
    gateway_delivery.set_deliver_fn(lambda job, content, **kw: "some error")
    try:
        assert gateway_delivery.deliver("hi") is False
    finally:
        gateway_delivery.set_deliver_fn(None)


def test_gateway_delivery_empty_text_false():
    assert gateway_delivery.deliver("   ") is False


# -- autonomous scheduling (registers a real cron job) ----------------------

def test_launcher_script_written(tmp_path, monkeypatch):
    import plugins.proactivity as p

    monkeypatch.setattr(p, "_home_dir", lambda: tmp_path / "proact")
    path = p._launcher_path()
    assert path.exists()
    assert "run_background_cycle" in path.read_text()


def test_schedule_registers_cron_job(tmp_path, monkeypatch):
    import plugins.proactivity as p

    monkeypatch.setattr(p, "_home_dir", lambda: tmp_path / "proact")
    calls = {}

    def fake_create_job(prompt, schedule, name=None, script=None, no_agent=False, **kw):
        calls.update(schedule=schedule, name=name, script=script, no_agent=no_agent)
        return {"id": "job123"}

    import cron.jobs
    monkeypatch.setattr(cron.jobs, "create_job", fake_create_job)
    msg = p.schedule_background_job(every_minutes=15)
    assert "job123" in msg
    assert calls["no_agent"] is True
    assert "15 minutes" in calls["schedule"]
    assert calls["script"].endswith("proactivity_tick.py")


def test_schedule_fail_soft_without_cron(tmp_path, monkeypatch):
    import plugins.proactivity as p

    monkeypatch.setattr(p, "_home_dir", lambda: tmp_path / "proact")
    import cron.jobs

    def boom(*a, **k):
        raise RuntimeError("no cron")

    monkeypatch.setattr(cron.jobs, "create_job", boom)
    msg = p.schedule_background_job()
    assert "proactivity run" in msg  # honest fallback message, never raises
