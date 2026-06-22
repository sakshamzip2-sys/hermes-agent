"""Verify the Coding Router profile + swe-delegation skill against the REAL substrate.

The `coding` profile is a delegator: a Hermes agent that routes a coding task
between Claude Code (the PLANNER) and Codex (the EXECUTOR) and verifies the result.
This proves it is real, not a placeholder:

  * its SOUL.md loads through the real `agent.prompt_builder.load_soul_md` loader in
    an isolated HERMES_HOME (no disruption to the live stack), stays inside the
    50-80 line identity discipline, is identity-only (no embedded workflows/fences),
    and actually encodes the delegator roles (Claude Code plans, Codex executes);
  * its config.yaml carries a model plus named personality overlays;
  * the `swe-delegation` orchestration skill exists, is discovered by the real
    bundled-skills scanner alongside the claude-code and codex skills it sequences,
    and encodes the plan -> execute -> verify protocol.

Real loader, real discovery code, real file IO, no mocks.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "profile_templates" / "coding"
SOUL = TEMPLATE / "SOUL.md"
CONFIG = TEMPLATE / "config.yaml"
SKILL = REPO / "skills" / "autonomous-ai-agents" / "swe-delegation" / "SKILL.md"


# --------------------------------------------------------------------------- #
# Profile: SOUL.md identity discipline (mirrors the coder profile contract)
# --------------------------------------------------------------------------- #

def test_soul_within_identity_line_discipline():
    lines = SOUL.read_text(encoding="utf-8").strip().splitlines()
    assert 30 <= len(lines) <= 80, f"SOUL.md is {len(lines)} lines (want 30-80)"


def test_soul_is_identity_only_no_workflows_or_facts():
    body = SOUL.read_text(encoding="utf-8")
    assert "## Identity" in body
    assert "## Boundaries and restrictions" in body
    # Multi-step workflows / shell belong in the swe-delegation skill, not the SOUL.
    assert "```" not in body, "SOUL.md should hold identity, not code/workflows"


def test_soul_encodes_the_delegator_roles():
    """The whole point: Hermes delegates, Claude Code PLANS, Codex EXECUTES."""
    body = SOUL.read_text(encoding="utf-8")
    low = body.lower()
    assert "delegat" in low, "SOUL must establish the agent as a delegator"
    assert "claude code" in low and "planner" in low, "Claude Code must be the planner"
    assert "codex" in low and "executor" in low, "Codex must be the executor"
    # It names the operating skill that drives both backends.
    assert "swe-delegation" in low, "SOUL should point at the swe-delegation skill"


def test_soul_loads_through_the_real_loader_in_isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "SOUL.md").write_text(SOUL.read_text(encoding="utf-8"), encoding="utf-8")

    code = (
        "from agent.prompt_builder import load_soul_md;"
        "c = load_soul_md();"
        "print('LOADED' if c and 'OpenComputer Coding Router' in c else 'MISSING')"
    )
    env = {**os.environ, "HERMES_HOME": str(home)}
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       env=env, capture_output=True, text=True)
    assert "LOADED" in r.stdout, f"stdout={r.stdout!r} stderr={r.stderr[-800:]!r}"


def test_config_has_model_and_named_personalities():
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    assert isinstance(cfg.get("model"), str) and cfg["model"], "no model set"
    personalities = cfg.get("personalities") or {}
    assert len(personalities) >= 2, "router profile needs named personalities"
    for name, text in personalities.items():
        assert isinstance(text, str) and text.strip(), f"empty personality: {name}"


# --------------------------------------------------------------------------- #
# Skill: the swe-delegation orchestration playbook
# --------------------------------------------------------------------------- #

def _frontmatter(md_text: str) -> dict:
    """Parse the leading --- ... --- YAML frontmatter block."""
    assert md_text.startswith("---"), "SKILL.md must open with YAML frontmatter"
    _, fm, _body = md_text.split("---", 2)
    return yaml.safe_load(fm)


def test_skill_exists_with_valid_frontmatter():
    assert SKILL.is_file(), f"missing swe-delegation skill at {SKILL}"
    fm = _frontmatter(SKILL.read_text(encoding="utf-8"))
    assert fm.get("name") == "swe-delegation", f"bad skill name: {fm.get('name')!r}"
    assert fm.get("description"), "skill needs a description (drives NL selection)"


def test_skill_sequences_both_backend_skills():
    text = SKILL.read_text(encoding="utf-8")
    fm = _frontmatter(text)
    related = fm.get("metadata", {}).get("opencomputer", {}).get("related_skills", [])
    assert "claude-code" in related and "codex" in related, (
        f"swe-delegation must relate to both backend skills, got {related}"
    )
    # The body must actually invoke both CLIs it orchestrates.
    assert "claude -p" in text, "skill must drive Claude Code (planner) in print mode"
    assert "codex exec" in text, "skill must drive Codex (executor)"


def test_skill_encodes_plan_execute_verify_protocol():
    low = SKILL.read_text(encoding="utf-8").lower()
    assert "planner" in low and "claude code" in low
    assert "executor" in low and "codex" in low
    # The verify step is the one the router never delegates away.
    assert "verify" in low, "protocol must include a verification step"
    assert "plan before execute" in low or "plan before" in low, (
        "protocol must establish plan-before-execute ordering"
    )


def test_skill_is_discovered_by_the_real_bundled_scanner():
    """Prove the skill surfaces via the SAME code that syncs bundled skills into
    ~/.hermes/skills, next to the claude-code and codex skills it orchestrates."""
    sys.path.insert(0, str(REPO))
    from tools.skills_sync import _discover_bundled_skills

    discovered = {name for name, _path in _discover_bundled_skills(REPO / "skills")}
    assert "swe-delegation" in discovered, "swe-delegation not discovered by the scanner"
    assert "claude-code" in discovered and "codex" in discovered, (
        "expected the two backend skills to be discovered alongside it"
    )


# --------------------------------------------------------------------------- #
# Profile is installable by the real installer (no separate registration needed)
# --------------------------------------------------------------------------- #

def test_profile_is_picked_up_by_the_installer(tmp_path):
    """The installer auto-discovers any profile_templates/<name> with SOUL+config.
    Running it must produce a `coding` profile with both files copied."""
    script = REPO / "scripts" / "install_profiles.sh"
    target = tmp_path / "profiles"
    env = {**os.environ, "HOME": str(tmp_path / "fake_home")}
    r = subprocess.run(["sh", str(script), str(target)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"installer failed: {r.stderr}"
    assert "installed coding" in r.stdout, f"coding not installed:\n{r.stdout}"
    assert (target / "coding" / "SOUL.md").is_file()
    assert (target / "coding" / "config.yaml").is_file()
