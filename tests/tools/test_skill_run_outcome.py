"""Skill execution outcome telemetry (2026-06-21).

record_run + usage_report give a REAL per-skill success rate and average latency
(used by the Skill Health view and the router tie-break). Never-run skills report
None so the UI shows "no data" instead of a misleading 0%.
"""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    (home / "skills").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import tools.skill_usage as mod
    importlib.reload(mod)
    return home


def _write_skill(skills_dir: Path, name: str):
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# body\n", encoding="utf-8"
    )


def test_record_run_tracks_success_failure_latency(skills_home):
    import tools.skill_usage as su
    _write_skill(skills_home / "skills", "alpha")
    su.record_run("alpha", success=True, latency_ms=100)
    su.record_run("alpha", success=True, latency_ms=300)
    su.record_run("alpha", success=False, latency_ms=200)
    row = {r["name"]: r for r in su.usage_report()}["alpha"]
    assert row["run_count"] == 3
    assert row["success_count"] == 2
    assert row["failure_count"] == 1
    assert row["success_rate"] == round(2 / 3, 4)
    assert row["avg_latency_ms"] == 200  # (100+300+200)/3
    assert row["last_error_at"] is not None
    assert row["last_run_at"] is not None


def test_never_run_skill_reports_no_data(skills_home):
    import tools.skill_usage as su
    _write_skill(skills_home / "skills", "beta")
    row = {r["name"]: r for r in su.usage_report()}["beta"]
    assert row["run_count"] == 0
    assert row["success_rate"] is None
    assert row["avg_latency_ms"] is None


def test_record_run_is_crash_safe(skills_home):
    import tools.skill_usage as su
    # Bad latency types must not raise.
    su.record_run("gamma", success=True, latency_ms=None)  # type: ignore[arg-type]
    su.record_run("gamma", success=True)
    rows = {r["name"]: r for r in su.usage_report()}
    # gamma has no SKILL.md on disk, so it won't appear in usage_report (which
    # scans disk); the point is record_run did not raise.
    assert "gamma" not in rows or rows["gamma"]["run_count"] >= 0
