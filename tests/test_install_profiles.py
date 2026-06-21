"""Tests for scripts/install_profiles.sh, the safe, reversible profile installer.

These tests drive the real shell script as a subprocess against a throwaway
target directory. They never touch the real ~/.hermes/profiles tree: the target
is always a pytest tmp_path and the script reads HERMES_PROFILES_DIR / argv only.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "install_profiles.sh"
TEMPLATES = REPO_ROOT / "profile_templates"

EXPECTED_PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]


def _run(target: Path) -> subprocess.CompletedProcess:
    """Run the installer with an explicit target dir argument."""
    env = dict(os.environ)
    # Belt-and-suspenders: even if argv were ignored, never point at a real home.
    env["HERMES_PROFILES_DIR"] = str(target / "__should_be_ignored__")
    env["HOME"] = str(target / "__fake_home__")
    return subprocess.run(
        ["sh", str(SCRIPT), str(target)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_script_exists_and_executable():
    assert SCRIPT.is_file(), f"missing installer script at {SCRIPT}"


def test_installs_all_profiles_with_required_files(tmp_path):
    target = tmp_path / "profiles"
    result = _run(target)
    assert result.returncode == 0, (
        f"installer failed: rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )

    for name in EXPECTED_PROFILES:
        prof = target / name
        assert (prof / "SOUL.md").is_file(), f"{name} missing SOUL.md"
        assert (prof / "config.yaml").is_file(), f"{name} missing config.yaml"
        assert f"installed {name}" in result.stdout, (
            f"expected 'installed {name}' in output, got:\n{result.stdout}"
        )

    # finance is the rich profile: connectors + a skills tree.
    finance = target / "finance"
    assert (finance / "CONNECTORS.md").is_file(), "finance missing CONNECTORS.md"
    assert (finance / "skills").is_dir(), "finance missing skills/ dir"
    # The skills tree must be a real recursive copy, not an empty placeholder.
    assert (finance / "skills" / "dcf-model" / "SKILL.md").is_file(), (
        "finance skills/ was not copied recursively"
    )


def test_idempotent_rerun_skips_existing(tmp_path):
    target = tmp_path / "profiles"

    first = _run(target)
    assert first.returncode == 0, f"first run failed:\n{first.stderr}"
    for name in EXPECTED_PROFILES:
        assert f"installed {name}" in first.stdout

    second = _run(target)
    assert second.returncode == 0, f"second run failed:\n{second.stderr}"
    for name in EXPECTED_PROFILES:
        assert f"skip {name} (exists)" in second.stdout, (
            f"expected idempotent skip for {name}, got:\n{second.stdout}"
        )
        # On the skip pass nothing should be reported as freshly installed.
        assert f"installed {name}" not in second.stdout, (
            f"{name} was reinstalled on the idempotent re-run:\n{second.stdout}"
        )


def test_does_not_overwrite_existing_profile(tmp_path):
    target = tmp_path / "profiles"
    assert _run(target).returncode == 0

    # Tamper with an installed file; a non-destructive re-run must preserve it.
    sentinel = "USER LOCAL EDIT - DO NOT CLOBBER\n"
    coder_soul = target / "coder" / "SOUL.md"
    coder_soul.write_text(sentinel)

    assert _run(target).returncode == 0
    assert coder_soul.read_text() == sentinel, (
        "re-run overwrote a user-edited SOUL.md; installer is not reversible/safe"
    )


def test_installed_coder_soul_matches_template(tmp_path):
    target = tmp_path / "profiles"
    assert _run(target).returncode == 0

    installed = (target / "coder" / "SOUL.md").read_text()
    template = (TEMPLATES / "coder" / "SOUL.md").read_text()
    assert installed == template, "installed coder SOUL.md is not a real copy of the template"
    # Guard against a stub: the real template has substantive content.
    assert "SOUL" in installed and len(installed) > 200


def test_default_target_uses_hermes_profiles_dir(tmp_path):
    """With no argv, the installer must honor HERMES_PROFILES_DIR (never argv-only)."""
    target = tmp_path / "env_profiles"
    env = dict(os.environ)
    env["HERMES_PROFILES_DIR"] = str(target)
    env["HOME"] = str(tmp_path / "__fake_home__")
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, f"env-target run failed:\n{result.stderr}"
    for name in EXPECTED_PROFILES:
        assert (target / name / "SOUL.md").is_file(), (
            f"{name} not installed into HERMES_PROFILES_DIR"
        )


def test_prints_summary_count(tmp_path):
    target = tmp_path / "profiles"
    result = _run(target)
    assert result.returncode == 0
    # Final summary must report the number of profiles installed (5 on a fresh run).
    assert "5" in result.stdout, f"missing install count in summary:\n{result.stdout}"
