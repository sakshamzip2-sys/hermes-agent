"""Live-wiring tests for the reconcile engine into the BACKGROUND turn path (GAP-2).

These prove the additive + gated wiring of the reconcile write engine
(``agent.memory_reconcile.reconcile``) into the live background turn path via
``agent.memory_reconcile_worker``:

  (a) with reconcile DISABLED (config enabled=false, or no config), the worker
      writes NOTHING to the holographic plane -- full back-compat, the engine
      never runs;

  (b) with reconcile ENABLED, given recent turn text that contains a durable
      fact, the background reconcile writes that fact to the holographic store
      out-of-band, and it is then recallable via ``search_facts_readonly``;

  (c) it is FAIL-SOFT: a reconcile error (a broken store handle) never raises
      into the caller -- the turn / background cycle keeps running.

Everything runs against TEMP stores under a pytest ``tmp_path`` -- no live
gateway, no live ~/.hermes, no live memory_store.db. No em dashes (house rule).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Repo root importable when run from anywhere.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.memory_reconcile_worker import (  # noqa: E402
    maybe_reconcile_turn,
    spawn_reconcile_turn,
)
from plugins.memory.holographic.store import MemoryStore  # noqa: E402


@pytest.fixture()
def store(tmp_path: Path) -> Iterator[MemoryStore]:
    """A fresh temp-DB holographic MemoryStore. Never touches the live store."""
    s = MemoryStore(db_path=str(tmp_path / "reconcile_live_test.db"))
    try:
        yield s
    finally:
        s.close()


def _all_contents(store: MemoryStore, source_store: str = "orchestrator/self") -> list[str]:
    """All currently-valid fact contents in the namespace (default view)."""
    rows = store._conn.execute(
        "SELECT content FROM facts WHERE source_store = ? AND t_invalid IS NULL",
        (source_store,),
    ).fetchall()
    return [r["content"] for r in rows]


# A durable, salient fact embedded in a realistic assistant turn. The user line
# is chatty (low-signal); the assistant line states the durable fact.
_USER_TURN = "Hey, what port does the gateway use again?"
_ASSISTANT_TURN = "The hermes gateway listens on port 8642 in the local stack."
_DURABLE_FACT = "The hermes gateway listens on port 8642 in the local stack."


# ===========================================================================
# (a) reconcile DISABLED => no holographic write (back-compat)
# ===========================================================================

def test_reconcile_disabled_writes_nothing(store: MemoryStore):
    # Default-OFF config: enabled is false.
    ops = maybe_reconcile_turn(
        store=store,
        config={"enabled": False},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert ops == []
    assert _all_contents(store) == []


def test_reconcile_no_config_writes_nothing(store: MemoryStore):
    # No config at all (None) is also a no-op.
    ops = maybe_reconcile_turn(
        store=store,
        config=None,
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert ops == []
    assert _all_contents(store) == []


def test_reconcile_disabled_spawn_returns_none(store: MemoryStore):
    # The background spawn is a no-op when the gate is closed: no thread.
    t = spawn_reconcile_turn(
        store=store,
        config={"enabled": False},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert t is None
    assert _all_contents(store) == []


def test_reconcile_enabled_but_no_store_writes_nothing():
    # Gate on but no holographic store handle => still a no-op (never raises).
    ops = maybe_reconcile_turn(
        store=None,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert ops == []


# ===========================================================================
# (b) reconcile ENABLED => durable fact written + recallable
# ===========================================================================

def test_reconcile_enabled_writes_durable_fact_recallable(store: MemoryStore):
    ops = maybe_reconcile_turn(
        store=store,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    # At least one op was emitted and the durable fact landed in the store.
    assert ops, "reconcile produced no ops with the gate on"
    contents = _all_contents(store)
    assert any("8642" in c for c in contents), (
        f"durable fact not written to holographic plane; got {contents!r}"
    )

    # It is recallable via the read-only path the MergeLayer uses (the same
    # plane), proving the out-of-band write is live and recallable.
    hits = store.search_facts_readonly(
        "gateway port 8642",
        min_trust=0.0,
        limit=10,
        or_expand=True,
        source_store="orchestrator/self",
    )
    assert any("8642" in str(h.get("content", "")) for h in hits), (
        f"durable fact not recallable via search_facts_readonly; got {hits!r}"
    )


def test_reconcile_enabled_via_background_spawn(store: MemoryStore):
    # The fire-and-forget daemon-thread path writes the same durable fact. We
    # join the returned thread so the assertion is deterministic.
    t = spawn_reconcile_turn(
        store=store,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert t is not None, "spawn returned no thread with the gate on"
    t.join(timeout=10.0)
    assert not t.is_alive(), "background reconcile thread did not finish"

    contents = _all_contents(store)
    assert any("8642" in c for c in contents), (
        f"background reconcile did not write the fact; got {contents!r}"
    )


def test_reconcile_enabled_idempotent_second_turn_noop(store: MemoryStore):
    # Same turn reconciled twice: the durable op-queue makes the second pass a
    # NOOP, so the store does not accumulate a duplicate (req #5 idempotence,
    # observed through the live wiring).
    maybe_reconcile_turn(
        store=store,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    before = _all_contents(store)
    n_before = sum(1 for c in before if "8642" in c)
    assert n_before == 1

    maybe_reconcile_turn(
        store=store,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    after = _all_contents(store)
    n_after = sum(1 for c in after if "8642" in c)
    assert n_after == 1, f"duplicate fact accumulated on re-run: {after!r}"


# ===========================================================================
# (c) fail-soft: a reconcile error never breaks the turn / cycle
# ===========================================================================

class _BrokenStore:
    """A store handle whose every method raises, to prove fail-soft behaviour.

    Exposes a ``_conn`` attribute that raises on use so reconcile's op-queue
    setup blows up; the worker must swallow it and return [].
    """

    @property
    def _conn(self):  # noqa: D401 - test stub
        raise RuntimeError("boom: store connection is dead")

    def search_facts_readonly(self, *args, **kwargs):  # pragma: no cover - safety
        raise RuntimeError("boom: read path is dead")

    def add_fact(self, *args, **kwargs):  # pragma: no cover - safety
        raise RuntimeError("boom: write path is dead")


def test_reconcile_failsoft_broken_store_does_not_raise():
    broken = _BrokenStore()
    # Must NOT raise despite the store exploding internally.
    ops = maybe_reconcile_turn(
        store=broken,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    assert ops == []


def test_reconcile_failsoft_background_spawn_does_not_raise():
    broken = _BrokenStore()
    t = spawn_reconcile_turn(
        store=broken,
        config={"enabled": True},
        user_text=_USER_TURN,
        response_text=_ASSISTANT_TURN,
    )
    # The thread starts (gate is on, store handle is present) but the worker
    # swallows the store error; joining must complete without an exception
    # escaping into this caller.
    assert t is not None
    t.join(timeout=10.0)
    assert not t.is_alive()
