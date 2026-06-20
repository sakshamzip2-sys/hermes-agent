"""Tests for the Part 2 / Slice 3 skill-health quality signal.

Proves the additive outcome-quality metrics on the skill_usage sidecar:
  (a) record_skill_outcome maintains a correct sample-count-weighted running
      mean for success_rate over multiple samples;
  (b) _empty_record back-compat — loading an old sidecar that predates the new
      fields backfills defaults with no crash;
  (c) skill_health_view returns the metrics and all four sort orders work;
  (d) apply_automatic_transitions behavior is UNCHANGED — a low success_rate
      never triggers a new archival.

All tests use a temp HERMES_HOME (never the live ~/.hermes store). No new
HERMES_* env vars are introduced; the fixture only points the existing
HERMES_HOME at a tmp dir, mirroring tests/tools/test_skill_usage.py.
"""

from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with a clean skills/ dir, reloaded per test.

    Mirrors the fixture in tests/tools/test_skill_usage.py: pins
    ``curator.prune_builtins`` OFF so provenance semantics are deterministic;
    individual tests flip it on when they need built-ins curation-eligible.
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "skills").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import tools.skill_usage as mod
    importlib.reload(mod)
    monkeypatch.setattr(mod, "_prune_builtins_enabled", lambda: False)
    return home


def _write_skill(skills_dir: Path, name: str, category: str = "") -> Path:
    """Create a minimal SKILL.md with a name: frontmatter field."""
    d = skills_dir / category / name if category else skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# body\n",
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# (a) record_skill_outcome — running mean over 3 samples
# ---------------------------------------------------------------------------

def test_record_skill_outcome_running_mean_over_3_samples(skills_home):
    import tools.skill_usage as su

    # Three turn_scores: 1.0, 0.0, 0.5  -> mean = 0.5
    su.record_skill_outcome("alpha", turn_score=1.0)
    rec = su.get_record("alpha")
    assert rec["sample_count"] == 1
    assert rec["success_rate"] == pytest.approx(1.0)

    su.record_skill_outcome("alpha", turn_score=0.0)
    rec = su.get_record("alpha")
    assert rec["sample_count"] == 2
    assert rec["success_rate"] == pytest.approx(0.5)

    su.record_skill_outcome("alpha", turn_score=0.5)
    rec = su.get_record("alpha")
    assert rec["sample_count"] == 3
    # (1.0 + 0.0 + 0.5) / 3
    assert rec["success_rate"] == pytest.approx(0.5)


def test_record_skill_outcome_all_metrics_running_mean(skills_home):
    import tools.skill_usage as su

    su.record_skill_outcome(
        "beta", turn_score=1.0, latency_ms=100.0, cost=0.02, user_rating=5.0
    )
    su.record_skill_outcome(
        "beta", turn_score=0.0, latency_ms=300.0, cost=0.04, user_rating=3.0
    )
    rec = su.get_record("beta")
    assert rec["sample_count"] == 2
    assert rec["success_rate"] == pytest.approx(0.5)
    assert rec["avg_latency_ms"] == pytest.approx(200.0)
    assert rec["cost_per_run"] == pytest.approx(0.03)
    assert rec["user_rating"] == pytest.approx(4.0)


def test_record_skill_outcome_partial_signal_leaves_others_unchanged(skills_home):
    import tools.skill_usage as su

    su.record_skill_outcome("gamma", turn_score=1.0, latency_ms=50.0)
    # Second call carries only latency: success_rate must NOT change, but the
    # latency mean folds in and sample_count increments.
    su.record_skill_outcome("gamma", latency_ms=150.0)
    rec = su.get_record("gamma")
    assert rec["sample_count"] == 2
    assert rec["success_rate"] == pytest.approx(1.0)  # unchanged
    assert rec["avg_latency_ms"] == pytest.approx(100.0)
    assert rec["cost_per_run"] is None
    assert rec["user_rating"] is None


def test_record_skill_outcome_empty_observation_is_noop(skills_home):
    import tools.skill_usage as su

    su.record_skill_outcome("delta")  # all None -> nothing recorded
    assert su.load_usage().get("delta") is None
    rec = su.get_record("delta")
    assert rec["sample_count"] == 0
    assert rec["success_rate"] is None


def test_record_skill_outcome_does_not_disturb_existing_counters(skills_home):
    import tools.skill_usage as su

    su.bump_use("epsilon")
    su.bump_view("epsilon")
    su.record_skill_outcome("epsilon", turn_score=0.8)
    rec = su.get_record("epsilon")
    assert rec["use_count"] == 1
    assert rec["view_count"] == 1
    assert rec["sample_count"] == 1
    assert rec["success_rate"] == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# (b) _empty_record back-compat — old sidecar without the new fields
# ---------------------------------------------------------------------------

def test_old_sidecar_without_quality_fields_backfills_defaults(skills_home):
    import tools.skill_usage as su

    # Simulate a sidecar written by an older Hermes: no quality fields at all.
    legacy = {
        "legacy-skill": {
            "created_by": "agent",
            "use_count": 7,
            "view_count": 2,
            "last_used_at": "2026-01-01T00:00:00+00:00",
            "patch_count": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
            "state": "active",
            "pinned": False,
        }
    }
    su._usage_file().write_text(json.dumps(legacy), encoding="utf-8")

    # load_usage must not crash and must preserve the legacy data.
    loaded = su.load_usage()
    assert loaded["legacy-skill"]["use_count"] == 7

    # get_record backfills the missing quality fields with defaults.
    rec = su.get_record("legacy-skill")
    assert rec["success_rate"] is None
    assert rec["avg_latency_ms"] is None
    assert rec["cost_per_run"] is None
    assert rec["user_rating"] is None
    assert rec["sample_count"] == 0
    # Existing data untouched.
    assert rec["use_count"] == 7


def test_record_outcome_on_legacy_record_starts_clean(skills_home):
    import tools.skill_usage as su

    legacy = {"legacy2": {"use_count": 3, "state": "active"}}
    su._usage_file().write_text(json.dumps(legacy), encoding="utf-8")

    # Recording an outcome on a legacy record (no sample_count) must treat it as
    # the first sample, not crash on the missing key.
    su.record_skill_outcome("legacy2", turn_score=0.6)
    rec = su.get_record("legacy2")
    assert rec["sample_count"] == 1
    assert rec["success_rate"] == pytest.approx(0.6)
    assert rec["use_count"] == 3  # preserved


# ---------------------------------------------------------------------------
# (c) skill_health_view — metrics present + four sort orders
# ---------------------------------------------------------------------------

def test_skill_health_view_returns_metrics(skills_home):
    import tools.skill_usage as su

    skills = skills_home / "skills"
    _write_skill(skills, "s-one")
    _write_skill(skills, "s-two")

    su.record_skill_outcome("s-one", turn_score=0.9, latency_ms=120.0, cost=0.01)
    # s-two has no samples.

    rows = su.skill_health_view()
    by_name = {r["name"]: r for r in rows}
    assert "s-one" in by_name and "s-two" in by_name

    one = by_name["s-one"]
    assert one["success_rate"] == pytest.approx(0.9)
    assert one["avg_latency_ms"] == pytest.approx(120.0)
    assert one["cost_per_run"] == pytest.approx(0.01)
    assert one["sample_count"] == 1
    # quality keys present even when unsampled.
    two = by_name["s-two"]
    assert two["success_rate"] is None
    assert two["sample_count"] == 0


def test_skill_health_view_four_sort_orders(skills_home):
    import tools.skill_usage as su

    skills = skills_home / "skills"
    for n in ("hi", "lo", "mid", "none"):
        _write_skill(skills, n)

    # hi: high success, cheap, heavily used.
    su.bump_use("hi"); su.bump_use("hi"); su.bump_use("hi")
    su.record_skill_outcome("hi", turn_score=0.95, cost=0.001)
    # lo: low success, used once.
    su.bump_use("lo")
    su.record_skill_outcome("lo", turn_score=0.10, cost=0.50)
    # mid: middle success, used twice.
    su.bump_use("mid"); su.bump_use("mid")
    su.record_skill_outcome("mid", turn_score=0.50, cost=0.05)
    # none: no samples, no use.

    rows = su.skill_health_view()

    most_used = [r["name"] for r in su.sort_skill_health(rows, "most_used")]
    assert most_used[0] == "hi"   # use_count 3 leads
    assert most_used.index("mid") < most_used.index("lo")  # 2 before 1

    most_successful = [r["name"] for r in su.sort_skill_health(rows, "most_successful")]
    assert most_successful[0] == "hi"
    # Unsampled 'none' sorts last (after every sampled row).
    assert most_successful[-1] == "none"
    assert most_successful.index("hi") < most_successful.index("mid") < most_successful.index("lo")

    most_failing = [r["name"] for r in su.sort_skill_health(rows, "most_failing")]
    assert most_failing[0] == "lo"  # lowest success first
    assert most_failing[-1] == "none"  # unsampled still last
    assert most_failing.index("lo") < most_failing.index("mid") < most_failing.index("hi")

    most_expensive = [r["name"] for r in su.sort_skill_health(rows, "most_expensive")]
    assert most_expensive[0] == "lo"  # cost_per_run 0.50 leads
    assert most_expensive[-1] == "none"  # unsampled (None cost) last
    assert most_expensive.index("lo") < most_expensive.index("mid") < most_expensive.index("hi")


def test_sort_skill_health_unknown_order_falls_back_to_most_used(skills_home):
    import tools.skill_usage as su

    skills = skills_home / "skills"
    _write_skill(skills, "a")
    _write_skill(skills, "b")
    su.bump_use("b")  # b used once, a never

    rows = su.skill_health_view()
    fallback = [r["name"] for r in su.sort_skill_health(rows, "not-a-real-order")]
    most_used = [r["name"] for r in su.sort_skill_health(rows, "most_used")]
    assert fallback == most_used
    assert fallback[0] == "b"


def test_sort_skill_health_does_not_mutate_input(skills_home):
    import tools.skill_usage as su

    skills = skills_home / "skills"
    _write_skill(skills, "x")
    _write_skill(skills, "y")
    rows = su.skill_health_view()
    snapshot = [r["name"] for r in rows]
    su.sort_skill_health(rows, "most_successful")
    assert [r["name"] for r in rows] == snapshot


# ---------------------------------------------------------------------------
# (d) apply_automatic_transitions UNCHANGED by quality metrics
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_low_success_rate_does_not_trigger_archival(skills_home, monkeypatch):
    """A recently-active skill with an abysmal success_rate must NOT archive.

    Quality is a read-only signal. The only thing that drives archival is
    inactivity (last_activity_at vs the archive cutoff), so a low success_rate on
    a fresh, recently-used skill must leave it active.
    """
    import tools.skill_usage as su
    import agent.curator as curator

    skills = skills_home / "skills"
    _write_skill(skills, "bad-but-fresh")

    now = datetime.now(timezone.utc)
    # Mark it agent-created and recently used so it is a curation candidate that
    # is NOT inactive.
    su.mark_agent_created("bad-but-fresh")
    su.bump_use("bad-but-fresh")
    # Terrible quality signal.
    su.record_skill_outcome("bad-but-fresh", turn_score=0.0)
    su.record_skill_outcome("bad-but-fresh", turn_score=0.0)

    counts = curator.apply_automatic_transitions(now=now)
    assert counts["archived"] == 0
    rec = su.get_record("bad-but-fresh")
    assert rec["state"] == su.STATE_ACTIVE


def test_quality_metrics_do_not_change_inactivity_archival_outcome(skills_home, monkeypatch):
    """Archival decision is identical with and without quality samples.

    Build an INACTIVE skill (last activity older than the archive cutoff) and
    confirm it archives the same whether or not it carries quality samples — the
    quality signal is never consulted by apply_automatic_transitions.
    """
    import tools.skill_usage as su
    import agent.curator as curator

    skills = skills_home / "skills"
    _write_skill(skills, "old-with-quality")
    _write_skill(skills, "old-no-quality")

    now = datetime.now(timezone.utc)
    archive_days = curator.get_archive_after_days()
    stale_ts = _iso(now - timedelta(days=archive_days + 5))

    # Two agent-created skills, both inactive past the archive cutoff. One has a
    # (good) quality signal, the other none.
    for name in ("old-with-quality", "old-no-quality"):
        su.mark_agent_created(name)
        su._mutate(name, lambda r: r.update({
            "last_used_at": stale_ts,
            "created_at": stale_ts,
        }))
    # Give one of them a great success_rate; this must NOT save it from archival.
    su._mutate("old-with-quality", lambda r: r.update({
        "last_used_at": stale_ts,  # re-assert: _mutate above set it
    }))
    su.record_skill_outcome("old-with-quality", turn_score=1.0)
    # record_skill_outcome must not touch activity timestamps.
    su._mutate("old-with-quality", lambda r: r.update({"last_used_at": stale_ts}))

    counts = curator.apply_automatic_transitions(now=now)

    # Both inactive skills archive regardless of quality data.
    assert su.get_record("old-with-quality")["state"] == su.STATE_ARCHIVED
    assert su.get_record("old-no-quality")["state"] == su.STATE_ARCHIVED
    assert counts["archived"] == 2


def test_record_skill_outcome_does_not_touch_activity_timestamps(skills_home):
    """record_skill_outcome must not bump last_used_at/last_viewed_at/last_patched_at.

    Activity timestamps drive the inactivity clock; if recording an outcome
    silently refreshed them it could prevent a legitimate archival. Prove it
    leaves them alone.
    """
    import tools.skill_usage as su

    su._mutate("ts-skill", lambda r: r.update({
        "last_used_at": "2025-01-01T00:00:00+00:00",
        "last_viewed_at": None,
        "last_patched_at": None,
    }))
    su.record_skill_outcome("ts-skill", turn_score=0.7, latency_ms=10.0)
    rec = su.get_record("ts-skill")
    assert rec["last_used_at"] == "2025-01-01T00:00:00+00:00"
    assert rec["last_viewed_at"] is None
    assert rec["last_patched_at"] is None
