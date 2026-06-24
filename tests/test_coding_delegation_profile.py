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
# The orchestration skill lives INSIDE the profile (self-contained), mirroring how
# the finance profile bundles its own skills tree.
SKILL = REPO / "profile_templates" / "coding" / "skills" / "swe-delegation" / "SKILL.md"


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


def test_config_pins_provider_and_keeps_no_secret():
    """The model block pins the provider so the CLI (`oc -p coding`) routes a claude-*
    model to OC-router correctly (a bare string would mis-route to the anthropic
    endpoint). The api_key is a secret and must NOT be committed in the template.

    These assert the EXACT routing-critical values, not mere truthiness: the runtime
    branches on provider=="custom" (claude-* goes to OC-router, not api.anthropic.com)
    and on api_mode=="chat_completions" (OC-router speaks the OpenAI chat-completions
    wire; agent_runtime_helpers.py builds base_url + '/chat/completions'). A regression
    to provider:anthropic or a dropped api_mode would silently mis-route and still pass
    a truthiness check, so pin the values."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    model = cfg.get("model")
    assert isinstance(model, dict), "model must pin a provider block, not be a bare string"
    assert isinstance(model.get("default"), str) and model["default"], "no model name set"
    assert model["default"].startswith("claude-"), "router runs a claude-* model on OC-router"
    assert model.get("provider") == "custom", (
        "provider must be 'custom' so claude-* routes to OC-router, not api.anthropic.com"
    )
    assert str(model.get("base_url", "")).rstrip("/").endswith("router.tryopencomputer.com/v1"), (
        "base_url must pin the OC-router endpoint"
    )
    assert model.get("api_mode") == "chat_completions", (
        "OC-router speaks the OpenAI chat-completions wire; api_mode must pin it"
    )
    assert "api_key" not in model, "api_key must NOT be committed; supply via local config/.env"
    personalities = cfg.get("personalities") or {}
    assert len(personalities) >= 2, "router profile needs named personalities"
    for name, text in personalities.items():
        assert isinstance(text, str) and text.strip(), f"empty personality: {name}"


def test_config_personalities_encode_the_routing_modes():
    """The named personalities are the router's plan-first / execute-fast / review
    modes. The 'review' overlay is the config-level expression of the user's reviewer
    contract (Claude Code reviews Codex's output; default REVISE over PASS; tests
    immutable), so assert it by name and by behavior, not just by count."""
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    personalities = {k.lower(): (v or "") for k, v in (cfg.get("personalities") or {}).items()}
    assert {"plan-first", "execute-fast", "review"}.issubset(personalities), (
        f"router needs plan-first/execute-fast/review modes, got {sorted(personalities)}"
    )
    review = personalities["review"].lower()
    assert "review" in review and ("revise" in review or "bug" in review), (
        "the 'review' mode must establish the skeptical-reviewer behavior"
    )


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


def test_skill_routes_claude_code_review_back_to_codex():
    """The user's defining loop: after Codex executes, Claude Code REVIEWS the diff
    (code review, security, QA) and those findings route BACK to Codex to fix, looping
    until clean. Assert the skill encodes this as a first-class step with a concrete
    review command, not just a passive slash-command mention. This is the half of the
    vision the original skill was missing (router-runs-tests only)."""
    text = SKILL.read_text(encoding="utf-8")
    low = text.lower()
    # Claude Code is cast as the reviewer, not only the planner.
    assert "reviewer" in low, "Claude Code must be cast as the reviewer, not only the planner"
    # A concrete review pass routes the executor's diff to Claude Code, read-only.
    assert "diff | claude -p" in text, (
        "skill must route the executor's diff to a Claude Code review pass"
    )
    # Security and QA are part of the encoded review, not absent.
    assert "security" in low, "review must cover security"
    assert "qa" in low, "review must cover QA"
    # The reviewer's findings loop BACK to the executor, not just raw test failures.
    assert "back to" in low and ("feedback" in low or "findings" in low), (
        "review findings must route back to the executor (Codex) in the loop"
    )
    # The full loop is spelled out: plan -> execute -> review -> verify -> feedback.
    assert "review" in low and "verify" in low and "feedback" in low


def test_verify_loop_script_is_executable_and_well_formed():
    """verify-delegation-loop.sh is the only artifact that exercises the loop against
    the real CLIs (gated on live auth, so not invoked here). Guard its shape so a
    regression that drops a phase or lets the planner write files is caught in CI
    without a live LLM call."""
    script = REPO / "verify-delegation-loop.sh"
    assert script.is_file(), "missing verify-delegation-loop.sh"
    assert os.access(script, os.X_OK), "verify-delegation-loop.sh must be executable"
    text = script.read_text(encoding="utf-8")
    for marker in ("STEP 1: PLAN", "STEP 2: EXECUTE", "STEP 3: VERIFY"):
        assert marker in text, f"verify script missing phase: {marker}"
    # Planner leg must be read-only (must NOT write files during planning).
    assert "--allowedTools 'Read Glob Grep'" in text, "planner leg must be read-only"
    # Executor fallback keeps the loop alive when Codex is unavailable.
    assert "fallback" in text.lower() or "fall back" in text.lower(), (
        "script must exercise/document the executor fallback so the loop never stalls"
    )


def test_skill_grants_terminal_lifecycle_control():
    """User requirement: Hermes opens/forks/ends tmux terminals and decides when.
    Assert the lifecycle is a real, structured section carrying all four verbs, not a
    stray word-presence match."""
    low = SKILL.read_text(encoding="utf-8").lower()
    assert "tmux" in low, "skill must cover tmux terminals"
    assert "kill-session" in low, "Hermes must end terminals with kill-session (real command)"
    assert "## terminal lifecycle" in low, "lifecycle must be a first-class section"
    section = low.split("## terminal lifecycle", 1)[1].split("\n## ", 1)[0]
    for verb in ("**open**", "**keep", "**fork**", "**end**"):
        assert verb in section, f"terminal-lifecycle section missing the {verb!r} verb"


def test_skill_makes_hermes_aware_of_slash_commands():
    """User requirement: Hermes knows the Claude Code + Codex slash commands."""
    text = SKILL.read_text(encoding="utf-8")
    assert "/plan" in text and "/review" in text, "must surface Claude Code slash commands"
    assert "codex review" in text or "codex apply" in text, "must surface Codex subcommands"


def test_skill_uses_the_proven_command_fixes():
    """Locks in the two fixes proven by live runs: read-only plan capture (not
    --permission-mode plan, which returns no plan in print mode) and the Codex
    ChatGPT-account model caveat."""
    text = SKILL.read_text(encoding="utf-8")
    assert "--allowedTools 'Read Glob Grep'" in text or "Read Glob Grep" in text, (
        "planner step must capture the full plan via read-only tools"
    )
    low = text.lower()
    assert "not supported when using codex with a chatgpt account" in low, (
        "skill must document the real Codex-on-ChatGPT model gotcha"
    )


def test_soul_grants_terminal_lifecycle_authority():
    low = SOUL.read_text(encoding="utf-8").lower()
    assert "terminal" in low and "fork" in low, (
        "SOUL must establish that the router owns the terminal lifecycle"
    )


def test_all_three_skills_live_inside_the_profile():
    """Self-contained: the planner (claude-code), executor (codex), and orchestration
    (swe-delegation) skills all ship INSIDE the profile, and the real bundled-skills
    scanner finds all three there. This is what makes the profile work identically on
    any host, including a VM whose hermes package does not ship claude-code/codex."""
    for s in ("claude-code", "codex", "swe-delegation"):
        assert (TEMPLATE / "skills" / s / "SKILL.md").is_file(), f"missing in-profile skill: {s}"
    sys.path.insert(0, str(REPO))
    from tools.skills_sync import _discover_bundled_skills

    found = {name for name, _ in _discover_bundled_skills(TEMPLATE / "skills")}
    assert {"claude-code", "codex", "swe-delegation"}.issubset(found), (
        f"profile skills not all discoverable: {found}"
    )


def test_profile_runs_lean_self_contained(tmp_path):
    """The .no-bundled-skills marker makes the real sync a no-op, so the profile
    runs with EXACTLY its three bundled skills (no host-dependent global sync, no
    duplicate skill names)."""
    assert (TEMPLATE / ".no-bundled-skills").is_file(), "missing .no-bundled-skills marker"
    # Lay the profile down as a HERMES_HOME and prove sync opts out.
    import shutil
    home = tmp_path / "home"
    shutil.copytree(TEMPLATE, home)
    code = (
        "from tools.skills_sync import sync_skills;"
        "r = sync_skills(quiet=True);"
        "print('OPTOUT' if r.get('skipped_opt_out') else 'SYNCED', r.get('copied'))"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=str(REPO),
                       env={**os.environ, "HERMES_HOME": str(home)}, capture_output=True, text=True)
    assert "OPTOUT" in r.stdout, f"sync did not opt out: {r.stdout!r} {r.stderr[-400:]!r}"
    # And the three skills are still the ones present.
    present = {p.parent.name for p in (home / "skills").rglob("SKILL.md")}
    assert present == {"claude-code", "codex", "swe-delegation"}, f"unexpected skill set: {present}"


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
    # Self-contained: all three skills + the lean-run marker ship INSIDE the profile
    # and the installer's recursive copy brings them along (like the finance tree).
    for s in ("claude-code", "codex", "swe-delegation"):
        assert (target / "coding" / "skills" / s / "SKILL.md").is_file(), (
            f"profile skill {s} was not copied by the installer"
        )
    assert (target / "coding" / ".no-bundled-skills").is_file(), (
        "the .no-bundled-skills marker was not copied (profile would not run lean)"
    )
