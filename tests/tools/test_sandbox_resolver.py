"""Tests for the sandbox-by-default backend resolver (tools/sandbox_resolver.py).

STEP 1 of the v2 upgrade plan: the terminal/execute_code default backend is now
``auto`` — it prefers a kernel-isolated sandbox (Docker, then Modal) and falls
back to ``local`` host execution (logged, never silent) when none is available.

These tests fully exercise the resolution logic by monkeypatching the probes, so
they pass on any machine. A separate, auto-skipped integration test asserts the
real isolation guarantee when a Docker daemon is actually present.
"""

import logging
import os
import shutil

import pytest

from tools import sandbox_resolver as sr


@pytest.fixture(autouse=True)
def _reset_resolver_cache():
    """The resolver caches its probe result per-process; clear it each test."""
    sr.reset_cache()
    yield
    sr.reset_cache()


# ---------------------------------------------------------------------------
# auto resolution: picks the best available isolated backend, else local
# ---------------------------------------------------------------------------

def test_auto_prefers_docker_when_available(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: True)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: True)  # docker still wins
    backend, was_auto = sr.resolve_terminal_backend("auto", write_back=False)
    assert backend == "docker"
    assert was_auto is True


def test_auto_falls_back_to_modal_when_no_docker(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: False)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: True)
    assert sr.resolve_terminal_backend("auto", write_back=False)[0] == "modal"


def test_auto_falls_back_to_local_when_nothing_available(monkeypatch, caplog):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: False)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: False)
    with caplog.at_level(logging.WARNING, logger=sr.logger.name):
        assert sr.resolve_terminal_backend("auto", write_back=False)[0] == "local"
    # The downgrade must be logged loudly, never silent.
    assert any("falling back" in r.message.lower() or "local host" in r.message.lower()
               for r in caplog.records)


# ---------------------------------------------------------------------------
# explicit backends pass through unchanged (full backward compat)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend", ["local", "docker", "modal", "singularity", "daytona", "ssh"])
def test_explicit_backend_passthrough(monkeypatch, backend):
    # Probes must NOT be consulted for an explicit choice.
    def _boom():
        raise AssertionError("probe must not run for explicit backend")
    monkeypatch.setattr(sr, "_probe_docker_available", _boom)
    monkeypatch.setattr(sr, "_probe_modal_configured", _boom)
    resolved, was_auto = sr.resolve_terminal_backend(backend, write_back=False)
    assert resolved == backend
    assert was_auto is False


def test_explicit_backend_case_and_whitespace_normalized(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: True)
    assert sr.resolve_terminal_backend("  DOCKER ", write_back=False)[0] == "docker"


# ---------------------------------------------------------------------------
# write-back concretizes the env var so literal readers never see "auto"
# ---------------------------------------------------------------------------

def test_write_back_sets_env(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: True)
    monkeypatch.setenv("TERMINAL_ENV", "auto")
    resolved, _ = sr.resolve_terminal_backend("auto", write_back=True)
    assert resolved == "docker"
    assert os.environ["TERMINAL_ENV"] == "docker"  # downstream readers see concrete


def test_write_back_disabled_leaves_env_untouched(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: False)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: False)
    monkeypatch.setenv("TERMINAL_ENV", "auto")
    sr.resolve_terminal_backend("auto", write_back=False)
    assert os.environ["TERMINAL_ENV"] == "auto"  # read-only probe must not mutate env


def test_resolve_backend_name_convenience(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: True)
    assert sr.resolve_backend_name("auto", write_back=False) == "docker"


def test_none_raw_reads_env_default_auto(monkeypatch):
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: False)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: False)
    monkeypatch.delenv("TERMINAL_ENV", raising=False)
    # Unset env => default "auto" => probes => local.
    assert sr.resolve_terminal_backend(None, write_back=False)[0] == "local"


# ---------------------------------------------------------------------------
# caching: the (potentially slow) probe runs at most once per process
# ---------------------------------------------------------------------------

def test_probe_runs_once_and_is_cached(monkeypatch):
    calls = {"docker": 0}

    def _count_docker():
        calls["docker"] += 1
        return False

    monkeypatch.setattr(sr, "_probe_docker_available", _count_docker)
    monkeypatch.setattr(sr, "_probe_modal_configured", lambda: False)
    for _ in range(5):
        assert sr.resolve_terminal_backend("auto", write_back=False)[0] == "local"
    assert calls["docker"] == 1  # cached after first probe


# ---------------------------------------------------------------------------
# the docker probe itself: which()-gated, never raises
# ---------------------------------------------------------------------------

def test_docker_probe_false_when_cli_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert sr._probe_docker_available() is False


def test_docker_probe_handles_subprocess_failure(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/docker")

    def _raise(*a, **k):
        raise OSError("boom")

    monkeypatch.setattr(sr.subprocess, "run", _raise)
    assert sr._probe_docker_available() is False  # swallows errors, returns False


# ---------------------------------------------------------------------------
# the resolved backend is one the approval gate recognises as isolated
# ---------------------------------------------------------------------------

def test_resolved_isolated_backend_is_gate_recognised(monkeypatch):
    """An auto-selected sandbox must match the gate's isolated-backend set, so
    sandboxed commands are correctly treated as already-isolated."""
    monkeypatch.setattr(sr, "_probe_docker_available", lambda: True)
    resolved, _ = sr.resolve_terminal_backend("auto", write_back=False)
    assert resolved in sr.ISOLATED_BACKENDS
    # Cross-check against the live approval gate's own classification.
    from tools import approval
    # check_dangerous_command short-circuits (returns allowed) for isolated backends.
    verdict = approval.check_dangerous_command("rm -rf /tmp/whatever", env_type=resolved)
    assert verdict is None or verdict.get("blocked") is not True


# ---------------------------------------------------------------------------
# INTEGRATION: real Docker daemon => real isolation (auto-skips without docker)
# ---------------------------------------------------------------------------

def _docker_really_available() -> bool:
    return sr._probe_docker_available()


@pytest.mark.integration
@pytest.mark.skipif(not _docker_really_available(),
                    reason="no running Docker daemon; isolation integration test skipped")
def test_auto_selects_docker_on_real_machine():
    """When a Docker daemon is actually running, auto must select it."""
    sr.reset_cache()
    assert sr.resolve_terminal_backend("auto", write_back=False)[0] == "docker"


# ---------------------------------------------------------------------------
# SECURITY: the auto cwd→/workspace bind-mount must refuse dangerous roots
# (else "sandbox by default" re-exposes ~/.ssh, ~/.aws, .env into the container)
# ---------------------------------------------------------------------------

def test_auto_mount_guard_refuses_home_and_root(tmp_path):
    from tools.terminal_tool import _is_safe_auto_mount_dir

    home = os.path.expanduser("~")
    # Dangerous: filesystem root, the home dir itself, and shallow shared roots.
    assert _is_safe_auto_mount_dir("/") is False
    assert _is_safe_auto_mount_dir(home) is False
    assert _is_safe_auto_mount_dir(os.path.dirname(home)) is False  # /Users or /home
    assert _is_safe_auto_mount_dir("/etc") is False
    assert _is_safe_auto_mount_dir("") is False


def test_auto_mount_guard_allows_real_project_dir(tmp_path):
    from tools.terminal_tool import _is_safe_auto_mount_dir

    # A normal nested project directory is safe to auto-mount.
    proj = tmp_path / "myrepo"
    proj.mkdir()
    assert _is_safe_auto_mount_dir(str(proj)) is True
    # And a typical home-nested repo path.
    assert _is_safe_auto_mount_dir(os.path.join(os.path.expanduser("~"), "code", "app")) is True


def test_auto_mount_guard_case_insensitive_home_bypass():
    """Round-4: on case-insensitive FS, an upper/mixed-case home path is the SAME
    dir and must be refused (inode comparison, not string compare)."""
    from tools.terminal_tool import _is_safe_auto_mount_dir
    import os as _os
    home = _os.path.expanduser("~")
    # These resolve to the same inode as home / /Users on macOS → must refuse.
    for variant in (home.upper(), "/USERS/" + _os.path.basename(home),
                    _os.path.dirname(home).upper()):
        # Only meaningful when the variant actually maps to the same dir
        # (case-insensitive FS). On a case-sensitive FS these are different dirs
        # that don't exist, so samefile→OSError→casefold compare still refuses
        # the home/Users cases.
        assert _is_safe_auto_mount_dir(variant) is False, variant


def test_auto_mount_guard_refuses_sensitive_home_children():
    """Round-4: dirs holding secrets directly under home must never auto-mount."""
    from tools.terminal_tool import _is_safe_auto_mount_dir
    import os as _os
    home = _os.path.expanduser("~")
    for child in (".ssh", ".aws", ".gnupg", ".config", ".kube", ".docker",
                  "Desktop", "Documents", "Downloads", "Library"):
        assert _is_safe_auto_mount_dir(_os.path.join(home, child)) is False, child
