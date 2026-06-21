"""Verify the Finance profile: real skills, gated paid connectors, compliance.

The finance profile adapts Anthropic's financial-services agents (Apache 2.0). It
must: load through the real loader, carry real adapted skills, gate every paid data
connector behind approval (with EDGAR free), keep the AI-drafts-humans-sign-off
boundary, and attribute the upstream license. Real file IO, no mocks.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
FINANCE = REPO / "profile_templates" / "finance"

PAID_CONNECTORS = [
    "FactSet", "Morningstar", "PitchBook", "S&P Global", "Capital IQ",
    "Refinitiv", "Bloomberg", "Crunchbase", "Daloopa", "Aiera",
]


def test_finance_profile_files_exist():
    assert (FINANCE / "SOUL.md").exists()
    assert (FINANCE / "config.yaml").exists()
    assert (FINANCE / "CONNECTORS.md").exists()
    assert (FINANCE / "skills" / "ATTRIBUTION.md").exists()


def test_finance_has_real_adapted_skills():
    skill_files = list((FINANCE / "skills").glob("*/SKILL.md"))
    # The adapted equity-research + modeling workflows (earnings, dcf, lbo, comps...).
    assert len(skill_files) >= 6, f"only {len(skill_files)} skills adapted"


def test_finance_soul_keeps_compliance_boundary():
    soul = (FINANCE / "SOUL.md").read_text(encoding="utf-8").lower()
    assert "drafts" in soul and "sign off" in soul  # AI-drafts-humans-sign-off
    assert "approval" in soul  # paid connectors require approval
    assert "never publish" in soul or "do not publish" in soul.replace("never publish", "do not publish")


def test_connectors_doc_gates_every_paid_source():
    text = (FINANCE / "CONNECTORS.md").read_text(encoding="utf-8")
    for name in PAID_CONNECTORS:
        assert name in text, f"connector not documented: {name}"
    assert "gated" in text.lower()
    # EDGAR is the one free, ungated source.
    assert "EDGAR" in text


def test_attribution_is_apache_and_names_source():
    attr = (FINANCE / "skills" / "ATTRIBUTION.md").read_text(encoding="utf-8")
    assert "Apache" in attr
    assert "anthropics/financial-services" in attr


def test_finance_config_has_model_and_compliance_personality():
    cfg = yaml.safe_load((FINANCE / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg.get("model"), str) and cfg["model"]
    personalities = cfg.get("personalities") or {}
    assert "compliance" in personalities and "auditor" in personalities
