"""Live-wiring tests for the MergeLayer into MemoryManager.prefetch_all (GAP-1 / GAP-4).

These prove the additive + gated wiring of the combine-on-read MergeLayer into
the live recall path (``MemoryManager.prefetch_all``):

  (a) with ``merge.enabled`` false, ``prefetch_all`` behaves EXACTLY as before
      (legacy per-provider concat) -- full back-compat, the merge path never
      runs and no trace is produced;
  (b) with ``merge.enabled`` true and a holographic store + session DB attached
      (temp), ``prefetch_all`` returns a merged block that, once passed through
      ``build_memory_context_block`` (the same fence the live call site applies),
      is fenced AND includes a fact present in the holographic store;
  (c) the RecallTrace is produced on the merge path (req #4 observability).

Everything runs against temp stores -- no live gateway, no live ~/.hermes. The
holographic store and SessionDB are created under a TemporaryDirectory. No em
dashes.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Repo root importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.memory_manager import (  # noqa: E402
    MemoryManager,
    build_memory_context_block,
)
from agent.memory_provider import MemoryProvider  # noqa: E402


# ---------------------------------------------------------------------------
# A minimal fake external provider whose prefetch returns a fixed string, so the
# legacy concat path is observable without a live backend. It is registered as a
# non-builtin external provider exactly like Honcho.
# ---------------------------------------------------------------------------

class _FakeProvider(MemoryProvider):
    def __init__(self, name: str, prefetch_text: str) -> None:
        self._name = name
        self._prefetch_text = prefetch_text

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:  # pragma: no cover
        return None

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        return self._prefetch_text

    def get_tool_schemas(self):
        return []

    def handle_tool_call(self, tool_name, args, **kwargs):  # pragma: no cover
        return "{}"


# ---------------------------------------------------------------------------
# (a) merge disabled => EXACT legacy concat behaviour (back-compat)
# ---------------------------------------------------------------------------

def test_merge_disabled_runs_legacy_concat_unchanged():
    mgr = MemoryManager()
    mgr.add_provider(_FakeProvider("alpha", "## Alpha\n- alpha fact"))

    # No attach_merge_planes call at all: the default install state.
    out = mgr.prefetch_all("what is the alpha fact", session_id="s1")

    assert out == "## Alpha\n- alpha fact"
    # The merge path never ran => no trace was recorded.
    assert mgr._last_recall_trace is None
    # The merge gate is closed (no config, no planes).
    assert mgr._merge_enabled() is False


def test_merge_config_present_but_disabled_still_legacy():
    # Even with planes attached, merge.enabled=false keeps the legacy path.
    with tempfile.TemporaryDirectory() as tmp:
        from hermes_state import SessionDB
        from plugins.memory.holographic.store import MemoryStore

        store = MemoryStore(db_path=Path(tmp) / "holo.db")
        store.add_fact("The deploy token lives in vault path kv/oc.", category="infra")
        db = SessionDB(db_path=Path(tmp) / "state.db")
        db.create_session("s1", source="api_server")
        try:
            mgr = MemoryManager()
            mgr.add_provider(_FakeProvider("alpha", "## Alpha\n- alpha fact"))
            mgr.attach_merge_planes(
                holographic_store=store,
                session_db=db,
                merge_config={"enabled": False},  # explicitly off
            )
            out = mgr.prefetch_all("deploy token vault path", session_id="s1")
            # Disabled => provider concat only, holographic fact NOT surfaced.
            assert out == "## Alpha\n- alpha fact"
            assert "vault path" not in out
            assert mgr._last_recall_trace is None
        finally:
            store.close()
            db.close()


# ---------------------------------------------------------------------------
# (b) merge enabled => merged + fenced block includes a holographic fact
# ---------------------------------------------------------------------------

def test_merge_enabled_returns_fenced_block_with_holographic_fact():
    with tempfile.TemporaryDirectory() as tmp:
        from hermes_state import SessionDB
        from plugins.memory.holographic.store import MemoryStore

        fact = "The hermes gateway listens on port 8642."
        store = MemoryStore(db_path=Path(tmp) / "holo.db")
        store.add_fact(fact, category="infra")

        db = SessionDB(db_path=Path(tmp) / "state.db")
        db.create_session("s1", source="api_server")
        db.append_message(
            "s1", role="user",
            content="Remember the hermes gateway port is 8642.",
        )
        try:
            mgr = MemoryManager()
            mgr.attach_merge_planes(
                holographic_store=store,
                session_db=db,
                merge_config={"enabled": True, "rrf_k": 60},
            )
            assert mgr._merge_enabled() is True

            raw = mgr.prefetch_all("hermes gateway port", session_id="s1")
            # The merge path returns RAW (un-fenced) text: no fence tags yet.
            assert raw, "merged recall returned empty"
            assert "<memory-context>" not in raw
            # The holographic fact is present in the merged block.
            assert "8642" in raw
            assert fact in raw

            # The live call site wraps the raw block through the whole-block
            # fence; that still runs in merge mode (belt-and-suspenders).
            fenced = build_memory_context_block(raw)
            assert fenced.startswith("<memory-context>")
            assert fenced.rstrip().endswith("</memory-context>")
            assert "8642" in fenced
            # The fence did NOT blank the block (no injection in the facts).
            assert "[BLOCKED" not in fenced
        finally:
            store.close()
            db.close()


def test_merge_enabled_holographic_only_no_session_db():
    # The holographic plane alone is enough to drive the merge path: a fact it
    # holds is recalled even with no session DB attached.
    with tempfile.TemporaryDirectory() as tmp:
        from plugins.memory.holographic.store import MemoryStore

        fact = "The primary database is named oc_primary on host db-1."
        store = MemoryStore(db_path=Path(tmp) / "holo.db")
        store.add_fact(fact, category="infra")
        try:
            mgr = MemoryManager()
            mgr.attach_merge_planes(
                holographic_store=store,
                merge_config={"enabled": True},
            )
            assert mgr._merge_enabled() is True
            raw = mgr.prefetch_all("primary database name host", session_id="")
            assert raw
            assert "oc_primary" in raw
        finally:
            store.close()


# ---------------------------------------------------------------------------
# (c) the RecallTrace is produced on the merge path
# ---------------------------------------------------------------------------

def test_merge_path_produces_recall_trace():
    with tempfile.TemporaryDirectory() as tmp:
        from hermes_state import SessionDB
        from plugins.memory.holographic.store import MemoryStore

        store = MemoryStore(db_path=Path(tmp) / "holo.db")
        store.add_fact("The hermes gateway listens on port 8642.", category="infra")
        db = SessionDB(db_path=Path(tmp) / "state.db")
        db.create_session("s1", source="api_server")
        db.append_message(
            "s1", role="user",
            content="The hermes gateway port is 8642.",
        )
        try:
            mgr = MemoryManager()
            mgr.attach_merge_planes(
                holographic_store=store,
                session_db=db,
                merge_config={"enabled": True},
            )
            mgr.prefetch_all("hermes gateway port", session_id="s1")

            trace = mgr._last_recall_trace
            assert trace is not None, "merge path did not record a RecallTrace"
            # Every documented trace key is present (req #4).
            for key in (
                "query", "expanded_query", "planes_queried", "planes_blocked",
                "planes_timed_out", "per_plane_hits", "fused_order",
                "final_slots", "per_plane_latency_ms", "total_latency_ms",
                "abstained",
            ):
                assert key in trace, f"trace missing key: {key}"
            # Both local planes were queried.
            assert "holographic" in trace["planes_queried"]
            assert "session" in trace["planes_queried"]
            # A real fused hit landed (not an abstention).
            assert trace["abstained"] is False
            assert trace["final_slots"]
        finally:
            store.close()
            db.close()


def test_merge_provider_plane_fused_alongside_local():
    # A registered provider's prefetch output is folded into the fusion as an
    # extra plane: its content surfaces in the merged block alongside the
    # holographic fact, and the provider plane appears in the trace.
    with tempfile.TemporaryDirectory() as tmp:
        from plugins.memory.holographic.store import MemoryStore

        store = MemoryStore(db_path=Path(tmp) / "holo.db")
        store.add_fact("The hermes gateway listens on port 8642.", category="infra")
        try:
            mgr = MemoryManager()
            mgr.add_provider(
                _FakeProvider("honcho", "## Identity\n- The user prefers concise answers.")
            )
            mgr.attach_merge_planes(
                holographic_store=store,
                merge_config={"enabled": True},
            )
            raw = mgr.prefetch_all("hermes gateway port answers", session_id="s1")
            assert raw
            # Holographic fact present.
            assert "8642" in raw
            # Provider content fused in too.
            assert "concise answers" in raw
            trace = mgr._last_recall_trace
            assert trace is not None
            assert "providers" in trace["planes_queried"]
        finally:
            store.close()
