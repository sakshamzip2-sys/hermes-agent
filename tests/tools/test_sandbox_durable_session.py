"""Tests for durable resumable sandbox sessions (CMA-style).

Covers the backend-agnostic reconnect *seam* (BaseEnvironment.handle /
BaseEnvironment.reconnect), per-session persistence of the sandbox handle in
``model_config`` JSON, and the ``reconnect_environment`` dispatch in
terminal_tool.  These let a resumed session reattach to the SAME sandbox
instead of spawning a fresh one — the foundation E2B plugs into later.
"""

import json

import pytest

from hermes_state import SessionDB
from tools.environments.base import BaseEnvironment


@pytest.fixture()
def db(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    yield d
    d.close()


class _FakeEnv(BaseEnvironment):
    """Minimal concrete BaseEnvironment for exercising the seam."""

    def cleanup(self):  # abstract in the base class
        pass


# ---------------------------------------------------------------------------
# A1 — the reconnect seam
# ---------------------------------------------------------------------------

def test_handle_default_is_none():
    """A backend that does not override `handle` is not reattachable."""
    env = _FakeEnv(cwd="/tmp", timeout=10)
    assert env.handle is None


def test_reconnect_default_returns_none():
    """The default `reconnect` cannot reattach and returns None, never raises."""
    assert BaseEnvironment.reconnect({"backend": "x"}, cwd="/tmp", timeout=10) is None


def test_subclass_can_expose_a_handle():
    """A backend overriding `handle` surfaces its reconnect token."""

    class _Reattachable(_FakeEnv):
        @property
        def handle(self):
            return {"backend": "fake", "id": "abc"}

    env = _Reattachable(cwd="/tmp", timeout=10)
    assert env.handle == {"backend": "fake", "id": "abc"}


# ---------------------------------------------------------------------------
# A2 — per-session persistence of the sandbox handle (model_config JSON)
# ---------------------------------------------------------------------------

def test_sandbox_handle_round_trips(db):
    db.create_session("s1", "api_server")
    db.set_session_sandbox_handle("s1", {"backend": "docker", "container_id": "c1"})
    assert db.get_session_sandbox_handle("s1") == {
        "backend": "docker",
        "container_id": "c1",
    }


def test_sandbox_handle_absent_is_none(db):
    db.create_session("s2", "api_server")
    assert db.get_session_sandbox_handle("s2") is None


def test_get_sandbox_handle_unknown_session_is_none(db):
    assert db.get_session_sandbox_handle("nope") is None


def test_sandbox_handle_none_clears(db):
    db.create_session("s3", "api_server")
    db.set_session_sandbox_handle("s3", {"backend": "docker"})
    db.set_session_sandbox_handle("s3", None)
    assert db.get_session_sandbox_handle("s3") is None


def test_sandbox_handle_survives_reopen(tmp_path):
    """The whole point: the handle outlives the process that wrote it."""
    p = tmp_path / "state.db"
    d1 = SessionDB(db_path=p)
    d1.create_session("s4", "api_server")
    d1.set_session_sandbox_handle("s4", {"backend": "docker", "container_id": "abc"})
    d1.close()

    d2 = SessionDB(db_path=p)
    try:
        assert d2.get_session_sandbox_handle("s4") == {
            "backend": "docker",
            "container_id": "abc",
        }
    finally:
        d2.close()


def test_sandbox_handle_preserves_other_model_config(db):
    """set/clear must not clobber sibling model_config keys."""
    db.create_session(
        "s5", "api_server", model_config={"_branched_from": "x", "model": "opus"}
    )
    db.set_session_sandbox_handle("s5", {"backend": "docker"})
    db.set_session_sandbox_handle("s5", None)
    mc = json.loads(db.get_session("s5")["model_config"])
    assert mc.get("_branched_from") == "x"
    assert mc.get("model") == "opus"
    assert "_sandbox_handle" not in mc


# ---------------------------------------------------------------------------
# A3 — reconnect dispatch + Docker handle/reconnect
# ---------------------------------------------------------------------------

from tools import terminal_tool  # noqa: E402
from tools.environments.docker import DockerEnvironment  # noqa: E402


def test_reconnect_environment_none_handle():
    assert terminal_tool.reconnect_environment(None, cwd="/x", timeout=10) is None


def test_reconnect_environment_empty_and_unknown():
    assert terminal_tool.reconnect_environment({}, cwd="/x", timeout=10) is None
    assert (
        terminal_tool.reconnect_environment({"backend": "nope"}, cwd="/x", timeout=10)
        is None
    )


def test_reconnect_environment_routes_to_backend(monkeypatch):
    seen = {}

    class _FakeBackend:
        @classmethod
        def reconnect(cls, handle, *, cwd, timeout, env=None):
            seen.update(handle=handle, cwd=cwd, timeout=timeout, env=env)
            return "REATTACHED"

    monkeypatch.setitem(terminal_tool._RECONNECT_BACKENDS, "fake", _FakeBackend)
    out = terminal_tool.reconnect_environment(
        {"backend": "fake", "id": "z"}, cwd="/work", timeout=42, env={"A": "1"}
    )
    assert out == "REATTACHED"
    assert seen == {
        "handle": {"backend": "fake", "id": "z"},
        "cwd": "/work",
        "timeout": 42,
        "env": {"A": "1"},
    }


def test_reconnect_environment_swallows_backend_errors(monkeypatch):
    class _Boom:
        @classmethod
        def reconnect(cls, handle, *, cwd, timeout, env=None):
            raise RuntimeError("daemon down")

    monkeypatch.setitem(terminal_tool._RECONNECT_BACKENDS, "boom", _Boom)
    assert (
        terminal_tool.reconnect_environment({"backend": "boom"}, cwd="/x", timeout=10)
        is None
    )


def test_docker_registered_for_reconnect():
    assert terminal_tool._RECONNECT_BACKENDS.get("docker") is DockerEnvironment


def _bare_docker(**attrs):
    env = DockerEnvironment.__new__(DockerEnvironment)
    env._container_id = attrs.get("container_id", "deadbeef")
    env._task_id = attrs.get("task_id", "default")
    env._persist_across_processes = attrs.get("persist", True)
    return env


def test_docker_handle_when_running():
    env = _bare_docker(container_id="deadbeef", task_id="sa-1-abcd")
    assert env.handle == {
        "backend": "docker",
        "task_id": "sa-1-abcd",
        "container_id": "deadbeef",
    }


def test_docker_handle_none_without_container():
    assert _bare_docker(container_id=None).handle is None


def test_docker_handle_none_when_not_persistent():
    # A non-persistent container is torn down on cleanup → nothing to reattach.
    assert _bare_docker(container_id="abc", persist=False).handle is None


def test_docker_reconnect_none_without_container_id():
    assert (
        DockerEnvironment.reconnect({"backend": "docker"}, cwd="/x", timeout=10) is None
    )


def test_docker_reconnect_none_when_container_dead(monkeypatch):
    monkeypatch.setattr(
        "tools.environments.docker._docker_container_alive", lambda cid: False
    )
    out = DockerEnvironment.reconnect(
        {"backend": "docker", "container_id": "gone", "task_id": "t"},
        cwd="/x",
        timeout=10,
    )
    assert out is None


def test_docker_reconnect_binds_to_known_container_when_alive(monkeypatch):
    """Happy path: a live container reattaches to THAT specific container_id —
    never via a `docker run` create (no TOCTOU placeholder-image run)."""
    monkeypatch.setattr(
        "tools.environments.docker._docker_container_alive", lambda cid: True
    )
    captured = {}

    def _fake_init(
        self,
        image,
        *,
        cwd,
        timeout,
        task_id,
        env,
        persist_across_processes,
        reuse_container_id=None,
        **kw,
    ):
        captured.update(
            image=image,
            cwd=cwd,
            timeout=timeout,
            task_id=task_id,
            reuse_container_id=reuse_container_id,
            persist=persist_across_processes,
        )
        # mimic the reattach binding the real __init__ performs
        self._container_id = reuse_container_id
        self._task_id = task_id
        self._persist_across_processes = persist_across_processes

    monkeypatch.setattr(DockerEnvironment, "__init__", _fake_init)
    out = DockerEnvironment.reconnect(
        {"backend": "docker", "container_id": "abc123", "task_id": "t1"},
        cwd="/work",
        timeout=30,
    )
    assert out is not None
    assert captured["reuse_container_id"] == "abc123"  # bound to the SPECIFIC container
    assert captured["task_id"] == "t1"
    assert captured["persist"] is True
    assert out.handle == {
        "backend": "docker",
        "task_id": "t1",
        "container_id": "abc123",
    }


def test_docker_reconnect_fails_closed_when_init_raises(monkeypatch):
    """If the container dies after the liveness check, reattach must return
    None (fail closed), never raise and never `docker run` a placeholder."""
    monkeypatch.setattr(
        "tools.environments.docker._docker_container_alive", lambda cid: True
    )

    def _boom(self, *a, **k):
        raise RuntimeError("container vanished mid-reattach")

    monkeypatch.setattr(DockerEnvironment, "__init__", _boom)
    out = DockerEnvironment.reconnect(
        {"backend": "docker", "container_id": "x", "task_id": "t"},
        cwd="/x",
        timeout=10,
    )
    assert out is None
