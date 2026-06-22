"""Tests for the gateway ``/agent`` slash command handler.

Exercises the handler logic (menu, bind, switch-back, validation, session
rotation) against a minimal stub of the gateway runner, with the persona
binding store redirected to a tmp dir via HERMES_HOME.
"""

import asyncio
from dataclasses import dataclass
from typing import List, Optional

import pytest

from gateway.slash_commands import GatewaySlashCommandsMixin
import gateway.persona_bindings as pb


@dataclass
class _FakePlatform:
    value: str


@dataclass
class _FakeSource:
    platform: _FakePlatform
    chat_id: str
    chat_type: str = "dm"
    thread_id: Optional[str] = None


class _FakeStore:
    def __init__(self):
        self.reset_calls: List[str] = []

    def reset_session(self, session_key, display_name=None):
        self.reset_calls.append(session_key)
        return None


class _FakeEvent:
    def __init__(self, args: str, source):
        self._args = args
        self.source = source

    def get_command_args(self) -> str:
        return self._args


class _StubRunner(GatewaySlashCommandsMixin):
    """Minimal carrier for the mixin method under test."""

    def __init__(self):
        self.session_store = _FakeStore()

    def _session_key_for_source(self, source) -> str:
        return f"key:{source.chat_id}"


def _run(stub, args, source):
    return asyncio.run(stub._handle_agent_command(_FakeEvent(args, source)))


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


def _src(chat_id="100"):
    return _FakeSource(_FakePlatform("telegram"), chat_id)


def test_menu_lists_gallery_and_default_state():
    stub = _StubRunner()
    out = _run(stub, "", _src())
    assert "finance" in out
    assert "default agent" in out.lower()
    # No binding was made, no session rotation.
    assert stub.session_store.reset_calls == []


def test_bind_sets_binding_and_rotates_session():
    stub = _StubRunner()
    src = _src("chatA")
    out = _run(stub, "finance", src)
    assert "finance" in out.lower()
    assert pb.get_bound_slug(src) == "finance"
    assert stub.session_store.reset_calls == ["key:chatA"]  # rotated once


def test_bind_is_case_insensitive():
    stub = _StubRunner()
    src = _src("chatCase")
    _run(stub, "Deep-Research", src)
    assert pb.get_bound_slug(src) == "deep-research"


def test_rebind_same_agent_is_noop_message_no_extra_reset():
    stub = _StubRunner()
    src = _src("chatB")
    _run(stub, "finance", src)
    out = _run(stub, "finance", src)
    assert "already chatting as" in out.lower()
    # Only the first bind rotated the session.
    assert stub.session_store.reset_calls == ["key:chatB"]


def test_off_clears_binding_and_rotates():
    stub = _StubRunner()
    src = _src("chatC")
    _run(stub, "legal", src)
    stub.session_store.reset_calls.clear()
    out = _run(stub, "off", src)
    assert "default agent" in out.lower()
    assert pb.get_bound_slug(src) is None
    assert stub.session_store.reset_calls == ["key:chatC"]


def test_off_when_not_bound():
    stub = _StubRunner()
    out = _run(stub, "off", _src("never"))
    assert "already using the default agent" in out.lower()
    assert stub.session_store.reset_calls == []


def test_unknown_agent_rejected():
    stub = _StubRunner()
    src = _src("chatD")
    out = _run(stub, "no-such-agent-xyz", src)
    assert "no specialized agent" in out.lower()
    assert pb.get_bound_slug(src) is None
    assert stub.session_store.reset_calls == []


def test_invalid_name_rejected():
    stub = _StubRunner()
    src = _src("chatE")
    out = _run(stub, "../evil", src)
    assert "not a valid agent name" in out.lower()
    assert pb.get_bound_slug(src) is None


def test_switch_between_agents():
    stub = _StubRunner()
    src = _src("chatF")
    _run(stub, "finance", src)
    out = _run(stub, "legal", src)
    assert pb.get_bound_slug(src) == "legal"
    assert "legal" in out.lower()
    # Two binds -> two rotations.
    assert stub.session_store.reset_calls == ["key:chatF", "key:chatF"]
