"""Prove the merge-plane wiring helper fires for EVERY agent-construction path.

The combine-on-read MergeLayer + holographic write plane is wired by a single
reusable helper, ``agent.agent_init.wire_memory_merge_planes``, so the three
agent-construction paths -- ``init_agent`` (interactive CLI), the ``-z`` oneshot
(``hermes_cli/oneshot.py``), and the gateway ``_create_agent``
(``gateway/platforms/api_server.py``) -- all attach the same planes. Before the
extraction the attach lived ONLY inside ``init_agent`` and the oneshot/gateway
paths silently bypassed it, so a ``hermes -z`` turn never recalled a holographic
fact even with ``memory.merge.enabled`` true.

These tests drive the helper directly against a constructed agent stub (a real
``MemoryManager`` + a real holographic ``MemoryStore`` + a real ``SessionDB``,
all under a temp ``HERMES_HOME``) and prove:

  (a) merge.enabled true  => the holographic plane is attached and the
      MergeLayer drives recall (``_merge_enabled()`` True; a seeded fact is
      recalled through ``prefetch_all``);
  (b) all gates off (the live default) => no-op: no plane attached, no
      ``memory_store.db`` created, recall stays on the legacy concat path;
  (c) ``_memory_manager is None`` => no-op, never raises;
  (d) the helper is idempotent: a second call with an already-attached plane
      does not re-open the store.

Everything runs against temp stores under a ``TemporaryDirectory`` HERMES_HOME --
no live gateway, no live ~/.hermes. No em dashes.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Repo root importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.agent_init import wire_memory_merge_planes  # noqa: E402
from agent.memory_manager import MemoryManager  # noqa: E402


class _AgentStub:
    """Minimal stand-in for AIAgent carrying only what the helper touches."""

    def __init__(self, memory_manager, session_db=None) -> None:
        self._memory_manager = memory_manager
        self._session_db = session_db
        # Defaults set by init_agent before the wiring runs.
        self._holographic_store = None
        self._reconcile_config = {}


# ---------------------------------------------------------------------------
# (a) merge enabled => holographic plane attached + MergeLayer drives recall
# ---------------------------------------------------------------------------

def test_helper_attaches_planes_when_merge_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            from hermes_state import SessionDB

            db = SessionDB(db_path=Path(tmp) / "state.db")
            db.create_session("s1", source="api_server")
            db.append_message(
                "s1", role="user",
                content="Remember the hermes gateway port is 8642.",
            )
            mgr = MemoryManager()
            agent = _AgentStub(mgr, session_db=db)
            try:
                config = {"memory": {"merge": {"enabled": True, "rrf_k": 60}}}
                wire_memory_merge_planes(agent, config)

                # The holographic write plane was stood up at $HERMES_HOME/memory_store.db.
                assert agent._holographic_store is not None
                assert (Path(tmp) / "memory_store.db").exists()
                # The MergeLayer is now live for recall.
                assert mgr._merge_enabled() is True

                # Seed a fact in the attached holographic plane and prove recall
                # routes through the MergeLayer.
                agent._holographic_store.add_fact(
                    "The hermes gateway listens on port 8642.", category="infra"
                )
                raw = mgr.prefetch_all("hermes gateway port", session_id="s1")
                assert raw
                assert "8642" in raw
            finally:
                db.close()
                if agent._holographic_store is not None:
                    agent._holographic_store.close()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home


def test_helper_attaches_plane_for_reconcile_only_without_read_attach():
    # Reconcile-only stands up the write plane but does NOT attach the read
    # path (merge stays on the legacy concat).
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            mgr = MemoryManager()
            agent = _AgentStub(mgr)
            config = {"memory": {"write": {"reconcile": {"enabled": True}}}}
            wire_memory_merge_planes(agent, config)
            try:
                # Write plane created + reconcile config stashed.
                assert agent._holographic_store is not None
                assert (Path(tmp) / "memory_store.db").exists()
                assert agent._reconcile_config.get("enabled") is True
                # Read path NOT attached: merge gate stays closed.
                assert mgr._merge_enabled() is False
            finally:
                if agent._holographic_store is not None:
                    agent._holographic_store.close()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home


# ---------------------------------------------------------------------------
# (b) all gates off (the live default) => no-op
# ---------------------------------------------------------------------------

def test_helper_noop_when_all_gates_disabled():
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            mgr = MemoryManager()
            agent = _AgentStub(mgr)

            # Empty config (the live default: merge/holographic/reconcile all absent).
            wire_memory_merge_planes(agent, {})

            assert agent._holographic_store is None
            assert mgr._merge_enabled() is False
            # No memory_store.db was created.
            assert not (Path(tmp) / "memory_store.db").exists()

            # An explicit-but-disabled config is also a no-op.
            wire_memory_merge_planes(
                agent,
                {"memory": {"merge": {"enabled": False}}},
            )
            assert agent._holographic_store is None
            assert not (Path(tmp) / "memory_store.db").exists()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home


# ---------------------------------------------------------------------------
# (c) no memory manager, gate ON => the helper lazily HOSTS a bare manager so the
#     local merge plane can drive recall without an external provider. This is
#     the fix for the live -z recall bug: init_agent only builds a manager for an
#     EXTERNAL provider, so with memory.provider empty (or on the -z oneshot)
#     _memory_manager is None and the old early-return left the whole MergeLayer
#     dark. The plane reads LOCAL stores (memory_store.db / session FTS5) which
#     need no provider, so the helper stands up a provider-less manager to host
#     them.
# ---------------------------------------------------------------------------

def test_helper_creates_manager_when_none_and_gate_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            agent = _AgentStub(None)
            # merge enabled but no manager => the helper creates a bare one and
            # attaches the local holographic plane so recall is live.
            wire_memory_merge_planes(
                agent,
                {"memory": {"merge": {"enabled": True}}},
            )
            assert agent._memory_manager is not None
            assert agent._holographic_store is not None
            assert (Path(tmp) / "memory_store.db").exists()
            assert agent._memory_manager._merge_enabled() is True
            # A provider-less manager has no registered providers.
            assert agent._memory_manager.providers == []

            # And the seeded fact is recalled through the MergeLayer end to end.
            try:
                agent._holographic_store.add_fact(
                    "The orphan staging host is host-mango-3190.", category="infra"
                )
                raw = agent._memory_manager.prefetch_all("orphan staging host")
                assert "host-mango-3190" in raw
            finally:
                agent._holographic_store.close()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home


def test_helper_noop_when_memory_manager_is_none_and_gates_off():
    # The gates-off path must stay a STRICT no-op even with no manager: no
    # manager is created, no store opened. This guards the default install.
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            agent = _AgentStub(None)
            wire_memory_merge_planes(agent, {"memory": {"merge": {"enabled": False}}})
            assert agent._memory_manager is None
            assert agent._holographic_store is None
            assert not (Path(tmp) / "memory_store.db").exists()

            # Empty config (no memory key at all) is also a no-op.
            wire_memory_merge_planes(agent, {})
            assert agent._memory_manager is None
            assert agent._holographic_store is None
            assert not (Path(tmp) / "memory_store.db").exists()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home


def test_helper_noop_when_config_is_none():
    # A None config must not raise and must be a no-op.
    mgr = MemoryManager()
    agent = _AgentStub(mgr)
    wire_memory_merge_planes(agent, None)
    assert agent._holographic_store is None
    assert mgr._merge_enabled() is False


# ---------------------------------------------------------------------------
# (d) idempotent: a second call does not re-open the store
# ---------------------------------------------------------------------------

def test_helper_is_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        prev_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmp
        try:
            mgr = MemoryManager()
            agent = _AgentStub(mgr)
            config = {"memory": {"merge": {"enabled": True}}}

            wire_memory_merge_planes(agent, config)
            first_store = agent._holographic_store
            assert first_store is not None
            try:
                # Second call is a no-op: the same store handle is retained,
                # not re-created.
                wire_memory_merge_planes(agent, config)
                assert agent._holographic_store is first_store
            finally:
                if agent._holographic_store is not None:
                    agent._holographic_store.close()
        finally:
            if prev_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_home
