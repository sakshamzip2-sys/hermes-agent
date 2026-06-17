"""Unit tests for the E2B sandbox backend (mocked SDK — no live E2B needed).

Mirrors tests/tools/test_daytona_environment.py: the E2B SDK is patched into
sys.modules so E2BEnvironment can be constructed and exercised without a real
sandbox / API key. The live smoke test (real isolation, pause/resume against a
running E2B endpoint) is the one thing these can't cover — that needs the 24/7
E2B container.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _FakeCommandExitError(Exception):
    """Mimics e2b's CommandExitError — carries the failed result."""

    def __init__(self, stdout="", stderr="", exit_code=1):
        super().__init__("command failed")
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


def _make_sandbox(sandbox_id="sb-123"):
    sb = MagicMock()
    sb.sandbox_id = sandbox_id
    # commands.run returns a background CommandHandle: .wait() yields the result,
    # .kill() terminates the COMMAND (never the sandbox). .stdout default for the
    # synchronous home/file-sync probes.
    handle = MagicMock()
    handle.stdout = ""
    handle.exit_code = 0
    handle.wait.return_value = SimpleNamespace(stdout="", stderr="", exit_code=0)
    sb.commands.run.return_value = handle
    sb.files.read.return_value = b""
    return sb


@pytest.fixture()
def e2b_sdk(monkeypatch):
    """Patch the e2b SDK module + lazy_deps so E2BEnvironment imports cleanly."""
    sandbox = _make_sandbox()
    mod = types.ModuleType("e2b")

    class _Sandbox:
        create = MagicMock(return_value=sandbox)
        connect = MagicMock(return_value=sandbox)

    mod.Sandbox = _Sandbox
    monkeypatch.setitem(sys.modules, "e2b", mod)
    # neutralize lazy dependency install
    import tools.lazy_deps as _ld
    monkeypatch.setattr(_ld, "ensure", lambda *a, **k: None)
    return SimpleNamespace(module=mod, Sandbox=_Sandbox, sandbox=sandbox)


@pytest.fixture()
def make_env(e2b_sdk):
    from tools.environments.e2b import E2BEnvironment

    created = []

    def _make(**kwargs):
        env = E2BEnvironment(task_id=kwargs.pop("task_id", "t-1"), **kwargs)
        created.append(env)
        return env

    yield _make
    for env in created:
        try:
            env.cleanup()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_create_calls_sandbox_create_with_template_and_timeout(make_env, e2b_sdk):
    make_env(image="base", sandbox_timeout=1234)
    e2b_sdk.Sandbox.create.assert_called()
    kwargs = e2b_sdk.Sandbox.create.call_args.kwargs
    assert kwargs.get("template") == "base"
    assert kwargs.get("timeout") == 1234


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def test_execute_returns_stdout_and_exit_code(make_env, e2b_sdk):
    env = make_env()
    e2b_sdk.sandbox.commands.run.return_value.wait.return_value = SimpleNamespace(
        stdout="hello\n", stderr="", exit_code=0
    )
    result = env.execute("echo hello")
    assert "hello" in result["output"]
    assert result["returncode"] == 0


def test_execute_surfaces_nonzero_exit_from_command_error(make_env, e2b_sdk):
    env = make_env()
    e2b_sdk.sandbox.commands.run.return_value.wait.side_effect = _FakeCommandExitError(
        stdout="oops\n", stderr="bad\n", exit_code=7
    )
    result = env.execute("false")
    assert result["returncode"] == 7
    assert "oops" in result["output"] or "bad" in result["output"]


def test_run_uses_background_command(make_env, e2b_sdk):
    env = make_env()
    e2b_sdk.sandbox.commands.run.return_value.wait.return_value = SimpleNamespace(
        stdout="", stderr="", exit_code=0
    )
    env.execute("echo hi")
    # The command exec must run as a background command so we can kill the
    # COMMAND (not the VM) on interrupt/timeout.
    assert any(
        c.kwargs.get("background")
        for c in e2b_sdk.sandbox.commands.run.call_args_list
    )


def test_cancel_kills_command_not_persistent_sandbox(make_env, e2b_sdk):
    """The bug that matters: a timeout/interrupt must kill the COMMAND, never
    the persistent sandbox (which would destroy the durable session)."""
    import threading

    env = make_env(persistent_filesystem=True)
    cmd_handle = MagicMock()
    started = threading.Event()
    release = threading.Event()

    def _blocking_wait(*a, **k):
        started.set()
        release.wait(2)
        return SimpleNamespace(stdout="", stderr="", exit_code=0)

    cmd_handle.wait.side_effect = _blocking_wait
    e2b_sdk.sandbox.commands.run.return_value = cmd_handle

    proc = env._run_bash("sleep 100")
    assert started.wait(2), "command did not start"
    proc.kill()  # simulates the timeout/interrupt path → cancel_fn
    release.set()

    cmd_handle.kill.assert_called()  # command terminated
    e2b_sdk.sandbox.kill.assert_not_called()  # sandbox (and filesystem) survives


# ---------------------------------------------------------------------------
# Durable reconnect seam
# ---------------------------------------------------------------------------

def test_handle_exposes_sandbox_id_when_persistent(make_env):
    env = make_env(task_id="task-9", persistent_filesystem=True)
    assert env.handle == {
        "backend": "e2b",
        "task_id": "task-9",
        "sandbox_id": "sb-123",
    }


def test_handle_none_when_not_persistent(make_env):
    env = make_env(persistent_filesystem=False)
    assert env.handle is None


def test_reconnect_uses_sandbox_connect(e2b_sdk):
    from tools.environments.e2b import E2BEnvironment

    env = E2BEnvironment.reconnect(
        {"backend": "e2b", "sandbox_id": "sb-555", "task_id": "t"},
        cwd="/home/user",
        timeout=30,
    )
    assert env is not None
    e2b_sdk.Sandbox.connect.assert_called_once()
    assert e2b_sdk.Sandbox.connect.call_args.args[0] == "sb-555"
    env.cleanup()


def test_reconnect_none_without_sandbox_id(e2b_sdk):
    from tools.environments.e2b import E2BEnvironment

    assert (
        E2BEnvironment.reconnect({"backend": "e2b"}, cwd="/x", timeout=10) is None
    )


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def test_cleanup_pauses_persistent_sandbox(e2b_sdk):
    from tools.environments.e2b import E2BEnvironment

    env = E2BEnvironment(task_id="t", persistent_filesystem=True)
    env.cleanup()
    e2b_sdk.sandbox.beta_pause.assert_called_once()
    e2b_sdk.sandbox.kill.assert_not_called()


def test_cleanup_kills_ephemeral_sandbox(e2b_sdk):
    from tools.environments.e2b import E2BEnvironment

    env = E2BEnvironment(task_id="t", persistent_filesystem=False)
    env.cleanup()
    e2b_sdk.sandbox.kill.assert_called_once()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def test_registered_as_isolatable_and_reconnectable():
    from tools import terminal_tool
    from tools.environments.e2b import E2BEnvironment
    from tools.sandbox_resolver import ISOLATED_BACKENDS

    assert "e2b" in ISOLATED_BACKENDS
    assert terminal_tool._RECONNECT_BACKENDS.get("e2b") is E2BEnvironment


def test_create_environment_dispatches_to_e2b(e2b_sdk):
    from tools import terminal_tool

    env = terminal_tool._create_environment(
        "e2b", image="base", cwd="/home/user", timeout=30,
        container_config={"e2b_sandbox_timeout": 900}, task_id="t-dispatch",
    )
    from tools.environments.e2b import E2BEnvironment

    assert isinstance(env, E2BEnvironment)
    env.cleanup()
