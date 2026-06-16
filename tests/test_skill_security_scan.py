"""Tests for the skill-security-scan helper's gating logic.

The summarize() verdict + exit code is the security-critical decision (a bug
here could let a malicious skill pass), so it is unit-tested against the real
SkillSpector JSON schema. No external tool is invoked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCAN_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills" / "skill-security-scan" / "scan.py"
)


@pytest.fixture(scope="module")
def scan():
    spec = importlib.util.spec_from_file_location("skill_security_scan", _SCAN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _report(score, severity, recommendation, issues):
    return {
        "skill": {"name": "t", "source": "/x"},
        "risk_assessment": {"score": score, "severity": severity, "recommendation": recommendation},
        "issues": issues,
    }


def test_critical_report_gates_unsafe(scan, capsys):
    rpt = _report(100, "CRITICAL", "DO_NOT_INSTALL", [
        {"id": "AST1", "severity": "HIGH", "pattern": "exec()", "category": "Dangerous Code",
         "location": {"file": "run.py", "start_line": 3}, "explanation": "arbitrary code"},
    ])
    assert scan.summarize(rpt) == 2
    out = capsys.readouterr().out
    assert "UNSAFE" in out and "AST1" in out


def test_high_severity_gates_unsafe_even_if_score_low(scan):
    # A single HIGH finding must gate, regardless of an under-threshold score.
    rpt = _report(20, "LOW", "SAFE", [
        {"id": "E2", "severity": "HIGH", "pattern": "Env Harvest", "category": "Exfil",
         "location": {"file": "a.py", "start_line": 1}},
    ])
    assert scan.summarize(rpt) == 2


def test_high_score_gates_unsafe_even_without_high_finding(scan):
    rpt = _report(75, "MEDIUM", "REVIEW", [
        {"id": "TR1", "severity": "MEDIUM", "pattern": "Broad Trigger", "category": "Trigger",
         "location": {"file": "SKILL.md", "start_line": 2}},
    ])
    assert scan.summarize(rpt) == 2  # score >= 60


def test_clean_report_is_safe(scan, capsys):
    assert scan.summarize(_report(5, "LOW", "SAFE", [])) == 0
    assert "SAFE" in capsys.readouterr().out


def test_medium_only_is_caution_but_not_gated(scan, capsys):
    # Matches SkillSpector's own scoring: a MEDIUM finding on a low score stays exit 0.
    rpt = _report(10, "LOW", "SAFE", [
        {"id": "RA2", "severity": "MEDIUM", "pattern": "Session Persistence", "category": "Rogue Agent",
         "location": {"file": "SKILL.md", "start_line": 68}},
    ])
    assert scan.summarize(rpt) == 0
    assert "CAUTION" in capsys.readouterr().out


def test_do_not_install_recommendation_gates_even_medium_only(scan):
    # SkillSpector band HIGH (score 51-59) with only MEDIUM findings still yields
    # recommendation=DO_NOT_INSTALL. The wrapper MUST gate (exit 2) — trust the
    # scanner's own verdict, not a re-derived score heuristic. (Closes the
    # 51-59 MEDIUM-only slip-through.)
    rpt = _report(52, "HIGH", "DO_NOT_INSTALL", [
        {"id": "TR1", "severity": "MEDIUM", "pattern": "Broad Trigger", "category": "Trigger",
         "location": {"file": "SKILL.md", "start_line": 2}},
        {"id": "EA3", "severity": "MEDIUM", "pattern": "Scope Creep", "category": "Agency",
         "location": {"file": "SKILL.md", "start_line": 3}},
    ])
    assert scan.summarize(rpt) == 2
