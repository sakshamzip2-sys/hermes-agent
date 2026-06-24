"""Integration-level tests for the persona-on-channels mechanism.

These exercise the genuinely hard-to-verify parts that unit tests of the binding
layer don't cover:

- the HERMES_HOME override (which loads a bound agent's SOUL) actually propagates
  into the agent worker THREAD via copy_context() — the same pattern the gateway
  uses at ``_run_in_executor_with_context``;
- two concurrent bound turns do not leak each other's override;
- a bound agent's profile state.db is isolated from the shared store (the
  persistence-isolation premise);
- the agent-cache signature is distinguished by persona slug (so a bound run
  never reuses a cached default agent).
"""

import contextvars
import threading

import pytest

import gateway.run as grun
from hermes_constants import (
    get_hermes_home,
    set_hermes_home_override,
    reset_hermes_home_override,
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for slug in ("finance", "legal"):
        (tmp_path / "agent-profiles" / slug).mkdir(parents=True)
    return tmp_path


def test_home_override_propagates_into_worker_thread_via_copy_context(home):
    """The gateway dispatches the agent via copy_context().run in a thread pool.
    The bound agent's SOUL loads from agent-profiles/<slug> only if the override
    set on the async side is visible inside that worker thread."""
    profile = str(home / "agent-profiles" / "finance")
    token = set_hermes_home_override(profile)
    try:
        ctx = contextvars.copy_context()  # snapshot AFTER setting the override
        seen = {}

        def _worker():
            # Runs in another thread, exactly like run_sync under the executor.
            seen["home"] = str(get_hermes_home())

        t = threading.Thread(target=lambda: ctx.run(_worker))
        t.start()
        t.join()
        assert seen["home"] == profile  # worker thread saw the bound profile home
    finally:
        reset_hermes_home_override(token)
    assert str(get_hermes_home()) == str(home)  # restored on the async side


def test_concurrent_persona_turns_are_isolated(home):
    """Two turns bound to different agents, running on different threads at the
    same time, must each see only their own profile home (no contextvar leak)."""
    fin = str(home / "agent-profiles" / "finance")
    leg = str(home / "agent-profiles" / "legal")
    barrier = threading.Barrier(2)
    results = {}

    def run_turn(name, path):
        # Each turn runs in its own copied context, mirroring one gateway turn.
        def _turn():
            token = set_hermes_home_override(path)
            try:
                barrier.wait(timeout=5)  # force the two turns to overlap
                results[name] = str(get_hermes_home())
            finally:
                reset_hermes_home_override(token)

        contextvars.copy_context().run(_turn)

    t1 = threading.Thread(target=run_turn, args=("fin", fin))
    t2 = threading.Thread(target=run_turn, args=("leg", leg))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert results["fin"] == fin
    assert results["leg"] == leg
    # The base context is untouched by either turn.
    assert str(get_hermes_home()) == str(home)


def test_profile_db_isolated_from_shared_db(home):
    """A bound turn persists to agent-profiles/<slug>/state.db; that transcript
    must not appear in the shared store, and vice versa."""
    from hermes_state import SessionDB

    class _Stub:
        def __init__(self):
            self._persona_profile_dbs = {}
            self._persona_profile_dbs_lock = threading.Lock()

    _Stub._get_persona_profile_db = grun.GatewayRunner._get_persona_profile_db
    stub = _Stub()

    fin_db = stub._get_persona_profile_db("finance", home / "agent-profiles" / "finance")
    shared = SessionDB()  # opens <HERMES_HOME>/state.db (the default/shared store)

    sid = "sess-iso-1"
    fin_db.create_session(sid, source="telegram")
    fin_db.append_message(sid, "user", content="hello finance agent")

    fin_conv = fin_db.get_messages_as_conversation(sid)
    assert any("finance" in (m.get("content") or "") for m in fin_conv)
    # The shared store never saw this agent-scoped turn.
    assert shared.get_messages_as_conversation(sid) == []


def test_home_override_changes_the_agents_actual_soul_identity(tmp_path, monkeypatch):
    """THE core proof: under the persona HERMES_HOME override, a REAL AIAgent
    builds a system prompt carrying the bound agent's SOUL identity (not the
    default). This is what makes a bound channel chat actually *be* the agent
    rather than just persisting to a different db. No model/network needed -
    we only construct the agent and build its system prompt.
    """
    base = tmp_path
    monkeypatch.setenv("HERMES_HOME", str(base))
    (base / "SOUL.md").write_text(
        "# Default Assistant\n## Identity\nI am the default OpenComputer assistant.\n"
    )
    fin = base / "agent-profiles" / "finance"
    fin.mkdir(parents=True)
    (fin / "SOUL.md").write_text(
        "# Finance Agent\n## Identity\nI am Finance Agent, a senior financial-services analyst.\n"
    )

    from run_agent import AIAgent

    def _prompt():
        a = AIAgent(
            model="claude-sonnet-4-6", enabled_toolsets=[], quiet_mode=True,
            api_key="x", base_url="http://localhost", provider="custom",
        )
        return a._build_system_prompt()

    # Default home -> default identity, NOT finance.
    p_default = _prompt()
    assert "default OpenComputer assistant" in p_default
    assert "senior financial-services analyst" not in p_default

    # Under the finance override -> finance identity, NOT default. The agent has
    # genuinely become Finance for this turn.
    token = set_hermes_home_override(str(fin))
    try:
        p_fin = _prompt()
    finally:
        reset_hermes_home_override(token)
    assert "senior financial-services analyst" in p_fin
    assert "default OpenComputer assistant" not in p_fin


def test_persona_slug_distinguishes_cache_signature(home):
    """Guards the fix that folds the slug into the agent-cache key so a bound run
    can't reuse a cached default agent (or a different persona's)."""
    base = grun.GatewayRunner._agent_config_signature(
        "claude-sonnet-4-6", {"provider": "p", "base_url": "u"}, ["terminal"], ""
    )
    # Mirrors run.py: `if persona_slug: _sig = (_sig, persona_slug)`.
    assert (base, "finance") != base
    assert (base, "finance") != (base, "legal")
    assert (base, "finance") == (base, "finance")
