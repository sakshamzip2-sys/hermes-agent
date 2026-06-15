"""Tests for proactivity cadence tuning + feedback classification (pure logic)."""

from __future__ import annotations

from plugins.proactivity.cadence import (
    CADENCE_MAX_PUSH_CAP,
    CadenceTuning,
    effective_push_cap,
    keyword_muted,
    load_cadence,
    save_cadence,
    step_cap,
)
from plugins.proactivity.feedback import classify_feedback, engagement_signal


# -- cadence ----------------------------------------------------------------

def test_step_cap_up_down_bounded():
    assert step_cap(1, "up", ceiling=3) == 2
    assert step_cap(0, "down", ceiling=3) == 0  # floor
    assert step_cap(3, "up", ceiling=3) == 3    # ceiling
    assert step_cap(2, "none", ceiling=3) == 2


def test_step_cap_never_exceeds_hard_max():
    assert step_cap(CADENCE_MAX_PUSH_CAP, "up", ceiling=100) == CADENCE_MAX_PUSH_CAP


def test_effective_push_cap_uses_config_when_unevolved():
    assert effective_push_cap(2, CadenceTuning()) == 2


def test_effective_push_cap_uses_tuned_value():
    assert effective_push_cap(2, CadenceTuning(push_cap=4)) == 4


def test_effective_push_cap_clamps_to_hard_max():
    assert effective_push_cap(99, CadenceTuning(push_cap=99)) == CADENCE_MAX_PUSH_CAP


def test_keyword_muted_substring_match():
    assert keyword_muted("calendar", "Team sync", ("calendar",)) is True
    assert keyword_muted("luma", "AI Meetup", ("calendar",)) is False
    assert keyword_muted("x", "y", ()) is False


def test_cadence_persist_roundtrip(tmp_path):
    t = CadenceTuning(push_cap=3, muted_keywords=("calendar", "luma"), decisions=5)
    save_cadence(tmp_path, t)
    loaded = load_cadence(tmp_path)
    assert loaded.push_cap == 3
    assert loaded.muted_keywords == ("calendar", "luma")
    assert loaded.decisions == 5


def test_load_cadence_missing_returns_default(tmp_path):
    assert load_cadence(tmp_path) == CadenceTuning()


# -- feedback ---------------------------------------------------------------

def test_classify_too_many():
    assert classify_feedback("stop reminding me so much") == "too_many"
    assert classify_feedback("too many notifications") == "too_many"


def test_classify_too_few():
    assert classify_feedback("you never check in on me") == "too_few"
    assert classify_feedback("wish you'd check in more") == "too_few"


def test_classify_mute_extracts_keyword():
    sig = classify_feedback("mute the calendar stuff")
    assert sig == ("mute", "calendar")


def test_classify_mute_rejects_stopword_keyword():
    # "mute the stuff" -> keyword would be a stopword -> not a mute
    assert classify_feedback("mute the stuff") != ("mute", "stuff")


def test_classify_none_for_normal_chat():
    assert classify_feedback("what's the weather today?") == "none"
    assert classify_feedback("") == "none"
    assert classify_feedback(None) == "none"


def test_engagement_signal_dead_band():
    assert engagement_signal(pushed=0, acked=0) == "healthy"
    assert engagement_signal(pushed=10, acked=1) == "too_many"   # 0.1 < 0.3
    assert engagement_signal(pushed=10, acked=9) == "too_few"    # 0.9 > 0.8
    assert engagement_signal(pushed=10, acked=5) == "healthy"    # 0.5 in band
