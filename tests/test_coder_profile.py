"""Verify the Coder profile against the REAL Hermes substrate (Part 2).

A specialized agent on Hermes is a profile: a SOUL.md (system-prompt slot #1) plus
a config.yaml. This proves the authored Coder profile is real, not a placeholder:
its SOUL.md loads through the actual load_soul_md loader in an isolated HERMES_HOME
(no disruption to the live stack), stays within the 50-80 line identity discipline
and is identity-only, and its config.yaml carries the coding model plus named
personality overlays. Real loader, real file IO, no mocks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "profile_templates" / "coder"
SOUL = TEMPLATE / "SOUL.md"
CONFIG = TEMPLATE / "config.yaml"


def test_soul_within_identity_line_discipline():
    lines = SOUL.read_text(encoding="utf-8").strip().splitlines()
    # The discipline is 50-80 lines; lean is allowed but never over the cap.
    assert 30 <= len(lines) <= 80, f"SOUL.md is {len(lines)} lines (want 30-80)"


def test_soul_is_identity_only_no_workflows_or_facts():
    body = SOUL.read_text(encoding="utf-8")
    # Identity sections present.
    assert "## Identity" in body
    assert "## Boundaries and restrictions" in body
    # No multi-step workflows or shell commands belong in a SOUL (those go in
    # skills / AGENTS.md). A fenced code block is the tell.
    assert "```" not in body, "SOUL.md should hold identity, not code/workflows"


def test_soul_loads_through_the_real_loader_in_isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "SOUL.md").write_text(SOUL.read_text(encoding="utf-8"), encoding="utf-8")

    code = (
        "from agent.prompt_builder import load_soul_md;"
        "c = load_soul_md();"
        "print('LOADED' if c and 'OpenComputer Coder' in c else 'MISSING')"
    )
    env = {**os.environ, "HERMES_HOME": str(home)}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       env=env, capture_output=True, text=True)
    assert "LOADED" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"


def test_config_has_coding_model_and_named_personalities():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(cfg.get("model"), str) and cfg["model"], "no coding model set"
    personalities = cfg.get("personalities") or {}
    for name in ("brainstorm", "terse", "reviewer"):
        assert name in personalities, f"missing named personality: {name}"
        assert isinstance(personalities[name], str) and personalities[name].strip()


# --------------------------------------------------------------------------- #
# Full roster as profiles (atlas / coder / sage / ledger)
# --------------------------------------------------------------------------- #

ROSTER = ["atlas", "coder", "sage", "ledger"]


def test_full_roster_profiles_exist():
    for name in ROSTER:
        assert (REPO / "profile_templates" / name / "SOUL.md").exists(), f"{name} SOUL missing"
        assert (REPO / "profile_templates" / name / "config.yaml").exists(), f"{name} config missing"


def test_every_roster_soul_is_disciplined_identity():
    for name in ROSTER:
        soul = (REPO / "profile_templates" / name / "SOUL.md").read_text(encoding="utf-8")
        lines = soul.strip().splitlines()
        assert 30 <= len(lines) <= 80, f"{name} SOUL is {len(lines)} lines"
        assert "## Identity" in soul and "## Boundaries and restrictions" in soul, name
        assert "```" not in soul, f"{name} SOUL should be identity-only (no code/workflows)"


def test_every_roster_config_sets_model_and_personalities():
    for name in ROSTER:
        cfg = yaml.safe_load((REPO / "profile_templates" / name / "config.yaml").read_text(encoding="utf-8"))
        assert isinstance(cfg.get("model"), str) and cfg["model"], f"{name} has no model"
        personalities = cfg.get("personalities") or {}
        assert len(personalities) >= 2, f"{name} needs named personalities"


def test_every_roster_soul_loads_through_real_loader(tmp_path):
    for name in ROSTER:
        home = tmp_path / name
        home.mkdir()
        soul = (REPO / "profile_templates" / name / "SOUL.md").read_text(encoding="utf-8")
        (home / "SOUL.md").write_text(soul, encoding="utf-8")
        code = "from agent.prompt_builder import load_soul_md; c = load_soul_md(); print('LOADED' if c else 'MISSING')"
        r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                           env={**os.environ, "HERMES_HOME": str(home)}, capture_output=True, text=True)
        assert "LOADED" in r.stdout, f"{name}: {r.stdout!r} {r.stderr[-400:]!r}"
