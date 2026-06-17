"""Per-subagent sandbox isolation switch (``delegation.subagent_sandbox``).

When set to ``isolated``, each delegated child gets its OWN sandbox instead of
sharing the parent's container — by reusing the existing per-task override hook
(``register_task_env_overrides`` → ``_resolve_container_task_id``).  Backend
itself is whatever the user runs (docker now, e2b later).
"""

import pytest

from tools import delegate_tool, terminal_tool


@pytest.fixture(autouse=True)
def _clean_overrides():
    yield
    for sid in list(terminal_tool._task_env_overrides.keys()):
        terminal_tool.clear_task_env_overrides(sid)


# --- _resolve_isolatable_backend -------------------------------------------
# NOTE: these are UNMOCKED — they exercise the real sandbox_resolver, which is
# exactly what catches the cross-session tuple-vs-string regression (the resolver
# returns (backend, was_auto) for resolve_terminal_backend but a plain string for
# resolve_backend_name; this code must use the string form).

def test_resolve_isolatable_backend_returns_isolated(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    assert delegate_tool._resolve_isolatable_backend() == "docker"


def test_resolve_isolatable_backend_none_for_local(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "local")
    assert delegate_tool._resolve_isolatable_backend() is None


def test_resolve_isolatable_backend_handles_tuple_returning_resolver(monkeypatch):
    """Regression: must not treat the resolver's (backend, was_auto) tuple as the
    backend name — that silently disabled per-subagent isolation."""
    monkeypatch.setenv("TERMINAL_ENV", "modal")
    out = delegate_tool._resolve_isolatable_backend()
    assert out == "modal"  # a clean string, never a tuple
    assert not isinstance(out, tuple)


# --- _maybe_isolate_child_sandbox ------------------------------------------

def test_shared_mode_no_isolation(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "shared"}
    )
    assert delegate_tool._maybe_isolate_child_sandbox("sa-1-aaaa") is False
    assert terminal_tool._resolve_container_task_id("sa-1-aaaa") == "default"


def test_default_mode_is_shared(monkeypatch):
    monkeypatch.setattr(delegate_tool, "_load_config", lambda: {})
    assert delegate_tool._maybe_isolate_child_sandbox("sa-2-bbbb") is False
    assert terminal_tool._resolve_container_task_id("sa-2-bbbb") == "default"


def test_isolated_mode_gives_child_its_own_sandbox(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "isolated"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: "docker")
    assert delegate_tool._maybe_isolate_child_sandbox("sa-3-cccc") is True
    # The EXISTING isolation hook now resolves to the child's own id, not "default".
    assert terminal_tool._resolve_container_task_id("sa-3-cccc") == "sa-3-cccc"
    assert terminal_tool._task_env_overrides["sa-3-cccc"]["env_type"] == "docker"


def test_isolated_mode_noop_when_backend_cannot_isolate(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "isolated"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: None)
    assert delegate_tool._maybe_isolate_child_sandbox("sa-4-dddd") is False
    assert terminal_tool._resolve_container_task_id("sa-4-dddd") == "default"


def test_unregister_clears_isolated_child_sandbox(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "isolated"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: "docker")
    delegate_tool._maybe_isolate_child_sandbox("sa-5-eeee")
    assert "sa-5-eeee" in terminal_tool._task_env_overrides

    # The critical side effect: the isolated child's sandbox is torn down
    # (force_remove=True), not just the override map cleared. Capture the call.
    cleanup_calls = []
    monkeypatch.setattr(
        terminal_tool,
        "cleanup_vm",
        lambda tid, **kw: cleanup_calls.append((tid, kw)),
    )

    delegate_tool._unregister_subagent("sa-5-eeee")
    assert "sa-5-eeee" not in terminal_tool._task_env_overrides
    assert ("sa-5-eeee", {"force_remove": True}) in cleanup_calls


def test_router_signal_isolates_even_when_config_shared(monkeypatch):
    # Global config is 'shared', but the child's GOAL signals long-running/isolated
    # work → the router escalates and the child gets its own sandbox.
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "shared"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: "docker")
    applied = delegate_tool._maybe_isolate_child_sandbox(
        "sa-7-gggg", goal="run an isolated long-running job for hours"
    )
    assert applied is True
    assert terminal_tool._resolve_container_task_id("sa-7-gggg") == "sa-7-gggg"


def test_no_router_signal_keeps_shared(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "shared"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: "docker")
    applied = delegate_tool._maybe_isolate_child_sandbox("sa-8-hhhh", goal="say hello")
    assert applied is False
    assert terminal_tool._resolve_container_task_id("sa-8-hhhh") == "default"


def test_router_signal_noop_without_isolatable_backend(monkeypatch):
    monkeypatch.setattr(
        delegate_tool, "_load_config", lambda: {"subagent_sandbox": "shared"}
    )
    monkeypatch.setattr(delegate_tool, "_resolve_isolatable_backend", lambda: None)
    applied = delegate_tool._maybe_isolate_child_sandbox(
        "sa-9-iiii", goal="run for hours in isolation"
    )
    assert applied is False


def test_unregister_shared_child_does_not_cleanup_vm(monkeypatch):
    """A shared-mode child registered no override → no sandbox teardown."""
    cleanup_calls = []
    monkeypatch.setattr(
        terminal_tool,
        "cleanup_vm",
        lambda tid, **kw: cleanup_calls.append((tid, kw)),
    )
    # never isolated → no override registered for this id
    delegate_tool._unregister_subagent("sa-6-ffff")
    assert cleanup_calls == []
