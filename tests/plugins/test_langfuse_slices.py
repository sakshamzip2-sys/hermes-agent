"""Tests for Part 2 Langfuse Slices 1-2 (DEFAULT-OFF, additive).

Slice 1 - cross-agent trace linkage: subagent_start opens a span nested under the
PARENT trace (reconstructed from the same deterministic seed the per-turn root used);
subagent_stop closes it.

Slice 2 - outcome-to-trace score bridge: on session end the bridge reads scored
turn_outcomes rows and calls create_score on the trace minted with the matching
(session, task) seed.

Both behaviors are gated behind config flags that default FALSE, so the default
behavior is unchanged. Every test here is hermetic: a MOCK Langfuse client (no live
Langfuse, no network), the outcomes ledger lives in a tmp SQLite file.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _fresh_plugin():
    """Import the langfuse plugin fresh so module-level caches start clean."""
    mod_name = "plugins.observability.langfuse"
    sys.modules.pop(mod_name, None)
    sys.modules.pop(mod_name + ".score_bridge", None)
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# A minimal Langfuse stand-in that records every nested call, so tests can
# assert the parent linkage and the score emission without a live server.
# ---------------------------------------------------------------------------
class FakeSpan:
    def __init__(self, recorder, *, name, trace_context, input, metadata):
        self.recorder = recorder
        self.name = name
        self.trace_context = trace_context
        self.input = input
        self.metadata = metadata
        self.ended = False
        self.update_kwargs = None

    def update(self, **kw):
        self.update_kwargs = kw

    def end(self, **kw):
        self.ended = True
        self.recorder["ended"].append(self)


class FakeClient:
    """Records create_trace_id seeds, nested observations, and scores."""

    def __init__(self, *, raise_on=None):
        # raise_on: a set of method names that should raise (fail-open tests).
        self.raise_on = raise_on or set()
        self.rec = {
            "trace_seeds": [],
            "observations": [],  # list[FakeSpan]
            "scores": [],        # list[dict]
            "ended": [],
            "flushed": 0,
        }

    def create_trace_id(self, seed=None):
        if "create_trace_id" in self.raise_on:
            raise RuntimeError("boom create_trace_id")
        self.rec["trace_seeds"].append(seed)
        # Deterministic, seed-derived (mirrors the real SDK's stable hashing).
        return f"trace::{seed}"

    def start_observation(self, *, trace_context=None, name=None, as_type=None,
                          input=None, metadata=None, **_):
        if "start_observation" in self.raise_on:
            raise RuntimeError("boom start_observation")
        span = FakeSpan(
            self.rec, name=name, trace_context=trace_context or {},
            input=input, metadata=metadata,
        )
        self.rec["observations"].append(span)
        return span

    def create_score(self, *, trace_id=None, name=None, value=None, data_type=None,
                     comment=None, **_):
        if "create_score" in self.raise_on:
            raise RuntimeError("boom create_score")
        self.rec["scores"].append(
            {"trace_id": trace_id, "name": name, "value": value,
             "data_type": data_type, "comment": comment}
        )

    def flush(self):
        self.rec["flushed"] += 1


# ===========================================================================
# Slice 1 - cross-agent trace linkage
# ===========================================================================
class TestCrossAgentLinkage:
    def _arm(self, monkeypatch, *, flags, client):
        plugin = _fresh_plugin()
        monkeypatch.setattr(plugin, "_langfuse_flags", lambda: flags)
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)
        return plugin

    def test_subagent_start_nests_child_under_parent_trace(self, monkeypatch):
        client = FakeClient()
        plugin = self._arm(
            monkeypatch,
            flags={"cross_agent": True, "score_bridge": False},
            client=client,
        )

        # The gateway mints the parent root with seed (session, task==session).
        parent_session = "sess-parent"
        expected_parent_trace = f"trace::{plugin.trace_id_seed(parent_session, parent_session)}"

        plugin.on_subagent_start(
            parent_session_id=parent_session,
            parent_turn_id="turn-1",
            parent_subagent_id="",
            child_session_id="sess-child",
            child_subagent_id="sub-7",
            child_role="researcher",
            child_goal="find the bug",
        )

        # Exactly one nested observation, opened UNDER the parent trace_id.
        obs = client.rec["observations"]
        assert len(obs) == 1, "expected one linkage span under the parent trace"
        span = obs[0]
        assert span.trace_context.get("trace_id") == expected_parent_trace
        assert span.metadata["child_session_id"] == "sess-child"
        assert span.metadata["parent_session_id"] == parent_session
        assert span.metadata["child_role"] == "researcher"
        assert "researcher" in span.name
        assert not span.ended  # still open until subagent_stop

    def test_subagent_stop_closes_the_child_span(self, monkeypatch):
        client = FakeClient()
        plugin = self._arm(
            monkeypatch,
            flags={"cross_agent": True, "score_bridge": False},
            client=client,
        )

        plugin.on_subagent_start(
            parent_session_id="sess-parent",
            child_session_id="sess-child",
            child_role="researcher",
            child_goal="find the bug",
        )
        span = client.rec["observations"][0]
        assert not span.ended

        plugin.on_subagent_stop(
            parent_session_id="sess-parent",
            child_session_id="sess-child",
            child_role="researcher",
            child_summary="found it",
            child_status="ok",
            duration_ms=1234,
        )

        assert span.ended, "subagent_stop must end the linkage span"
        assert span.update_kwargs is not None
        assert span.update_kwargs.get("output") == "found it"
        assert span.update_kwargs["metadata"]["child_status"] == "ok"
        assert span.update_kwargs["metadata"]["duration_ms"] == 1234
        # The live link map is reclaimed on stop.
        assert "sess-child" not in plugin._CHILD_LINK_STATE


# ===========================================================================
# Slice 2 - outcome-to-trace score bridge
# ===========================================================================
class TestScoreBridge:
    def _seed_outcomes(self, tmp_path, *, session_id, scores):
        """Write scored turns into a tmp outcomes DB; return its path."""
        from plugins.outcomes.store import OutcomesStore

        db = tmp_path / "outcomes.db"
        store = OutcomesStore(db)
        for i, sc in enumerate(scores):
            store.record(
                session_id=session_id,
                turn=f"turn-{i}",
                turn_score=sc,
                composite=sc,
                judge=None,
                ts=1000.0 + i,
            )
        return db

    def test_bridge_emits_create_score_on_matching_trace_id(self, tmp_path, monkeypatch):
        plugin = _fresh_plugin()
        from plugins.observability.langfuse import score_bridge

        session_id = "sess-bridge"
        db = self._seed_outcomes(tmp_path, session_id=session_id, scores=[0.9, 0.3])

        client = FakeClient()
        # task == session_id per the gateway reality.
        emitted = score_bridge.flush_session_scores(
            client, session_id=session_id, task=session_id, db_path=db,
        )

        assert emitted == 2
        expected_trace = f"trace::{plugin.trace_id_seed(session_id, session_id)}"
        assert {s["trace_id"] for s in client.rec["scores"]} == {expected_trace}
        assert {round(s["value"], 3) for s in client.rec["scores"]} == {0.9, 0.3}
        assert all(s["name"] == "turn_score" for s in client.rec["scores"])
        assert all(s["data_type"] == "NUMERIC" for s in client.rec["scores"])
        assert client.rec["flushed"] >= 1

    def test_session_end_handler_runs_bridge_when_enabled(self, tmp_path, monkeypatch):
        plugin = _fresh_plugin()
        session_id = "sess-end"
        db = self._seed_outcomes(tmp_path, session_id=session_id, scores=[0.7])

        client = FakeClient()
        monkeypatch.setattr(
            plugin, "_langfuse_flags",
            lambda: {"cross_agent": False, "score_bridge": True},
        )
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)

        # Point the bridge at the tmp DB via the store's default-path seam.
        import plugins.outcomes.store as store_mod
        monkeypatch.setattr(store_mod, "default_db_path", lambda: db)

        plugin.on_session_end_score_bridge(session_id=session_id, task_id=session_id)

        expected_trace = f"trace::{plugin.trace_id_seed(session_id, session_id)}"
        assert len(client.rec["scores"]) == 1
        assert client.rec["scores"][0]["trace_id"] == expected_trace
        assert round(client.rec["scores"][0]["value"], 3) == 0.7


# ===========================================================================
# Default-OFF: both behaviors are no-ops when their flags are false (default).
# ===========================================================================
class TestDefaultOff:
    def test_cross_agent_noop_when_flag_off(self, monkeypatch):
        plugin = _fresh_plugin()
        client = FakeClient()
        monkeypatch.setattr(
            plugin, "_langfuse_flags",
            lambda: {"cross_agent": False, "score_bridge": False},
        )
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)

        plugin.on_subagent_start(
            parent_session_id="p", child_session_id="c", child_role="r", child_goal="g",
        )
        plugin.on_subagent_stop(
            parent_session_id="p", child_session_id="c", child_summary="s",
        )

        assert client.rec["observations"] == []
        assert client.rec["ended"] == []
        assert plugin._CHILD_LINK_STATE == {}

    def test_score_bridge_noop_when_flag_off(self, tmp_path, monkeypatch):
        plugin = _fresh_plugin()
        # Seed a row so the only thing stopping a score is the flag.
        from plugins.outcomes.store import OutcomesStore
        db = tmp_path / "outcomes.db"
        OutcomesStore(db).record(
            session_id="s", turn="t", turn_score=0.5, ts=1.0,
        )
        import plugins.outcomes.store as store_mod
        monkeypatch.setattr(store_mod, "default_db_path", lambda: db)

        client = FakeClient()
        monkeypatch.setattr(
            plugin, "_langfuse_flags",
            lambda: {"cross_agent": False, "score_bridge": False},
        )
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)

        plugin.on_session_end_score_bridge(session_id="s", task_id="s")

        assert client.rec["scores"] == []
        assert client.rec["trace_seeds"] == []

    def test_flags_default_false_with_no_config(self, monkeypatch):
        """With no config.yaml block, both flags read False (default-off contract)."""
        plugin = _fresh_plugin()

        # Force a fresh read (bypass the TTL cache) and a config with no langfuse block.
        monkeypatch.setattr(plugin, "_CFG_CACHE", ({}, 0.0))
        import hermes_cli.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config", lambda: {})

        flags = plugin._langfuse_flags()
        assert flags == {"cross_agent": False, "score_bridge": False}


# ===========================================================================
# Fail-open: a client error is swallowed; the turn/flush still completes.
# ===========================================================================
class TestFailOpen:
    def test_subagent_start_swallows_client_error(self, monkeypatch):
        plugin = _fresh_plugin()
        client = FakeClient(raise_on={"start_observation"})
        monkeypatch.setattr(
            plugin, "_langfuse_flags",
            lambda: {"cross_agent": True, "score_bridge": False},
        )
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)

        # Must NOT raise - the delegation continues.
        plugin.on_subagent_start(
            parent_session_id="p", child_session_id="c", child_role="r", child_goal="g",
        )
        # Nothing got registered, and a subsequent stop is also a safe no-op.
        assert plugin._CHILD_LINK_STATE == {}
        plugin.on_subagent_stop(parent_session_id="p", child_session_id="c")

    def test_score_bridge_swallows_create_score_error(self, tmp_path, monkeypatch):
        plugin = _fresh_plugin()
        from plugins.observability.langfuse import score_bridge

        session_id = "sess-fail"
        db = tmp_path / "outcomes.db"
        from plugins.outcomes.store import OutcomesStore
        store = OutcomesStore(db)
        store.record(session_id=session_id, turn="t0", turn_score=0.8, ts=1.0)
        store.record(session_id=session_id, turn="t1", turn_score=0.4, ts=2.0)

        client = FakeClient(raise_on={"create_score"})
        # The whole flush must complete (return an int), never raise.
        emitted = score_bridge.flush_session_scores(
            client, session_id=session_id, task=session_id, db_path=db,
        )
        assert emitted == 0  # every create_score raised, all swallowed

    def test_session_end_handler_swallows_errors(self, tmp_path, monkeypatch):
        plugin = _fresh_plugin()
        session_id = "sess-fail2"
        db = tmp_path / "outcomes.db"
        from plugins.outcomes.store import OutcomesStore
        OutcomesStore(db).record(session_id=session_id, turn="t", turn_score=0.6, ts=1.0)
        import plugins.outcomes.store as store_mod
        monkeypatch.setattr(store_mod, "default_db_path", lambda: db)

        client = FakeClient(raise_on={"create_score"})
        monkeypatch.setattr(
            plugin, "_langfuse_flags",
            lambda: {"cross_agent": False, "score_bridge": True},
        )
        monkeypatch.setattr(plugin, "_get_langfuse", lambda: client)

        # Must not raise even though create_score blows up.
        plugin.on_session_end_score_bridge(session_id=session_id, task_id=session_id)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
