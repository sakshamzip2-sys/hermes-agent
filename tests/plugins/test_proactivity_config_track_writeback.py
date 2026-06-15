"""Tests for proactivity config (default-OFF invariant), /track parsing, writeback."""

from __future__ import annotations

import plugins.proactivity as proactivity
from plugins.proactivity import writeback
from plugins.proactivity.config import load_proactivity_config


# -- config: the protected default-OFF invariant ----------------------------

def test_proactivity_disabled_by_default():
    cfg = load_proactivity_config({})
    assert cfg.enabled is False  # INVARIANT: default-OFF, consent-gated


def test_config_overrides():
    cfg = load_proactivity_config({"enabled": True, "push_cap_per_day": 3})
    assert cfg.enabled is True
    assert cfg.push_cap_per_day == 3


def test_quiet_hours_wraparound():
    cfg = load_proactivity_config({"quiet_start_hour": 22, "quiet_end_hour": 8})
    assert cfg.in_quiet_hours(23) is True   # after start
    assert cfg.in_quiet_hours(2) is True    # before end (wrapped)
    assert cfg.in_quiet_hours(14) is False  # daytime
    assert cfg.in_quiet_hours(8) is False   # exactly end (exclusive)


def test_quiet_hours_same_start_end_never_quiet():
    cfg = load_proactivity_config({"quiet_start_hour": 0, "quiet_end_hour": 0})
    assert cfg.in_quiet_hours(0) is False
    assert cfg.in_quiet_hours(12) is False


# -- /track parsing ---------------------------------------------------------

def test_parse_track_bare_title_ends_now():
    title, ends_at = proactivity._parse_track("infra meetup")
    assert title == "infra meetup"
    assert ends_at is not None


def test_parse_track_with_duration_future():
    import time

    title, ends_at = proactivity._parse_track("dentist in 2h")
    assert title == "dentist"
    assert ends_at > time.time() + 3600  # ~2h ahead


def test_parse_track_minutes_and_days():
    _, e_min = proactivity._parse_track("x in 30m")
    _, e_day = proactivity._parse_track("y in 1d")
    assert e_min is not None and e_day is not None
    assert e_day > e_min


def test_parse_track_empty():
    assert proactivity._parse_track("") == (None, None)


def test_handle_track_creates_event(tmp_path, monkeypatch):
    monkeypatch.setattr(proactivity, "_home_dir", lambda: tmp_path / "proactivity")
    out = proactivity._handle_track("book club in 3h")
    assert "book club" in out
    store = proactivity._store()
    events = store.all_events()
    assert len(events) == 1
    assert events[0].title == "book club"


# -- writeback --------------------------------------------------------------

def test_write_checkin_reply(tmp_path, monkeypatch):
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(writeback, "get_memory_dir", lambda: mem)
    assert writeback.write_checkin_reply("the meetup", "It was great!") is True
    content = (mem / "MEMORY.md").read_text(encoding="utf-8")
    assert "the meetup" in content and "It was great!" in content


def test_write_checkin_reply_dedups(tmp_path, monkeypatch):
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(writeback, "get_memory_dir", lambda: mem)
    writeback.write_checkin_reply("x", "same reply")
    assert writeback.write_checkin_reply("x", "same reply") is False  # dup


def test_write_checkin_reply_rejects_empty_and_overlong(tmp_path, monkeypatch):
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(writeback, "get_memory_dir", lambda: mem)
    assert writeback.write_checkin_reply("x", "") is False
    assert writeback.write_checkin_reply("", "reply") is False
    assert writeback.write_checkin_reply("x", "z" * 700) is False  # over 600 chars
