"""Runtime tests for the gateway persona application helpers.

These exercise the two new GatewayRunner helpers in isolation (without standing
up a full gateway) plus the HERMES_HOME override mechanism that loads a bound
agent's SOUL/membrane — the core of running a channel turn as a specialized agent.
"""

import threading
from dataclasses import dataclass
from typing import Optional

import pytest

import gateway.run as grun
import gateway.persona_bindings as pb


@dataclass
class _FakePlatform:
    value: str


@dataclass
class _FakeSource:
    platform: _FakePlatform
    chat_id: str
    thread_id: Optional[str] = None


class _StubRunner:
    """Bare carrier for the unbound GatewayRunner helper methods under test.

    The real (unbound) methods are attached below so we exercise the production
    code, not a reimplementation.
    """

    def __init__(self):
        self._persona_profile_dbs = {}
        self._persona_profile_dbs_lock = threading.Lock()


# Bind the real (unbound) methods onto the stub so we test the actual code.
_StubRunner._resolve_chat_persona = grun.GatewayRunner._resolve_chat_persona
_StubRunner._get_persona_profile_db = grun.GatewayRunner._get_persona_profile_db


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # A real shipped agent the catalog/agent_exists recognizes is "finance";
    # give it an agent-profiles dir so resolution + db creation work.
    (tmp_path / "agent-profiles" / "finance").mkdir(parents=True)
    return tmp_path


def _src(chat_id="100"):
    return _FakeSource(_FakePlatform("telegram"), chat_id)


def test_resolve_returns_none_when_unbound(home):
    stub = _StubRunner()
    slug, pdir = stub._resolve_chat_persona(_src("unbound"))
    assert slug is None and pdir is None


def test_resolve_returns_slug_and_profile_dir_when_bound(home):
    src = _src("boundchat")
    assert pb.set_bound_slug(src, "finance")
    stub = _StubRunner()
    slug, pdir = stub._resolve_chat_persona(src)
    assert slug == "finance"
    assert pdir == home / "agent-profiles" / "finance"


def test_resolve_ignores_binding_to_nonexistent_agent(home, monkeypatch):
    # Force a binding to a slug with no template/profile/manifest: resolve must
    # fail closed to the default agent rather than point at a missing dir.
    src = _src("ghost")
    monkeypatch.setattr(pb, "agent_exists", lambda s: False)
    # The binding stores fine (format-valid), but resolve must reject it because
    # the agent does not exist -> fail closed to the default agent.
    assert pb.set_bound_slug(src, "finance")
    stub = _StubRunner()
    slug, pdir = stub._resolve_chat_persona(src)
    assert slug is None and pdir is None


def test_get_persona_profile_db_creates_and_caches(home):
    stub = _StubRunner()
    pdir = home / "agent-profiles" / "finance"
    db1 = stub._get_persona_profile_db("finance", pdir)
    assert db1 is not None
    assert (pdir / "state.db").is_file()
    # Cached: same object on second call (one sqlite connection per slug).
    db2 = stub._get_persona_profile_db("finance", pdir)
    assert db1 is db2


def test_two_agents_get_distinct_dbs(home):
    stub = _StubRunner()
    (home / "agent-profiles" / "legal").mkdir(parents=True)
    db_fin = stub._get_persona_profile_db("finance", home / "agent-profiles" / "finance")
    db_legal = stub._get_persona_profile_db("legal", home / "agent-profiles" / "legal")
    assert db_fin is not db_legal


def test_home_override_loads_profile_dir(home):
    # The mechanism that loads a bound agent's SOUL: override HERMES_HOME to the
    # profile dir for the turn, then restore it.
    from hermes_constants import (
        get_hermes_home,
        set_hermes_home_override,
        reset_hermes_home_override,
    )

    base = get_hermes_home()
    assert base == home
    profile = home / "agent-profiles" / "finance"
    token = set_hermes_home_override(str(profile))
    try:
        assert get_hermes_home() == profile  # turn now resolves SOUL/memory here
    finally:
        reset_hermes_home_override(token)
    assert get_hermes_home() == home  # restored after the turn
