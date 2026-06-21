"""Background reconcile trigger (GAP-2 live wiring) - additive + GATED.

This is the LIVE seam that runs the reconcile engine (``agent.memory_reconcile``)
on the BACKGROUND path, off the hot turn path, fully fail-soft. It mirrors how
the dreaming / background-review passes hook in: a completed turn's recent text
is mined for salient durable-fact candidates, those candidates are run through
the reconcile pipeline (redact -> retrieve-similar -> ADD/UPDATE/NOOP), and the
durable facts are written OUT-OF-BAND to the holographic plane (the same
``memory_store.db`` the MergeLayer reads), independent of the registered Honcho
provider.

The whole thing is GATED by ``memory.write.reconcile.enabled`` (default False).
When the gate is off, ``maybe_reconcile_turn`` returns immediately and nothing
is written, so live behaviour is UNCHANGED and the existing test baseline stays
green. When on, it runs in the BACKGROUND (caller dispatches on a daemon thread)
so it never delays a user turn, and every step is wrapped fail-soft so a
reconcile error never breaks the turn or the cycle.

Candidate extraction here is DELIBERATELY deterministic and LLM-free (sentence
split + a salient-shape filter): the reconcile engine is model-optional and the
goal of this wave is to prove the LIVE wiring, not to add an LLM round trip on
the background path. A capable extraction model can be slotted in later without
changing this contract (``reconcile`` already accepts a ``model=`` arg).

No em dashes (house rule). Pyright-clean.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any, List, Optional, Sequence

logger = logging.getLogger("hermes.agent.memory_reconcile_worker")

# Serializes background reconcile passes so two overlapping turn-ends do not
# race on the same store connection. A non-blocking acquire means a second
# trigger while one is in flight is simply skipped (the next turn re-triggers).
_reconcile_lock = threading.Lock()

# Sentence / line splitter for candidate extraction. Splits on sentence
# terminators and newlines; the reconcile engine's own store/not-store policy
# (transient / low-signal / empty) does the real filtering downstream.
_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# A candidate must carry SOME substance to be worth reconciling. Shorter than
# this (after strip) is dropped before it even reaches the engine; the engine
# would NOOP it anyway, this just avoids the op-queue churn.
_MIN_CANDIDATE_CHARS = 12

# Cap how many candidates one turn contributes, so a pathologically long turn
# cannot enqueue an unbounded reconcile batch on the background thread.
_MAX_CANDIDATES_PER_TURN = 24


def _extract_candidates(*texts: str) -> List[str]:
    """Deterministically split recent-turn text into salient fact candidates.

    LLM-free: split each text into sentences/lines, strip, and keep the ones
    with enough substance. The reconcile engine applies the real store/not-store
    + injection + redaction policy, so this only needs to be a cheap pre-filter.
    Order-preserving and de-duplicated (first occurrence wins) so a repeated
    line does not enqueue twice within one turn.
    """
    out: List[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for piece in _SPLIT_RE.split(text):
            cand = (piece or "").strip()
            if len(cand) < _MIN_CANDIDATE_CHARS:
                continue
            key = " ".join(cand.lower().split())
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= _MAX_CANDIDATES_PER_TURN:
                return out
    return out


def reconcile_now(
    store: Any,
    candidates: Sequence[str],
    *,
    source_store: str = "orchestrator/self",
    model: Any = None,
) -> list:
    """Run the reconcile pipeline over ``candidates`` against ``store``.

    Thin fail-soft wrapper around :func:`agent.memory_reconcile.reconcile`. Any
    exception is swallowed and logged at debug (the background path must never
    raise into the caller). Returns the list of ``OpRecord`` on success, or an
    empty list on a no-op / failure.
    """
    if store is None or not candidates:
        return []
    try:
        from agent.memory_reconcile import reconcile as _reconcile

        return _reconcile(
            list(candidates),
            store,
            source_store=source_store,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - background; never surface
        logger.debug("reconcile worker: reconcile failed (fail-soft): %s", exc)
        return []


def maybe_reconcile_turn(
    *,
    store: Any,
    config: Optional[dict],
    user_text: str = "",
    response_text: str = "",
    source_store: str = "orchestrator/self",
    model: Any = None,
) -> list:
    """Reconcile a completed turn's salient facts, GATED and fail-soft.

    This is the GAP-2 live entry point. It is a no-op (returns ``[]``) unless
    ``config['enabled']`` is truthy AND a holographic ``store`` handle is
    present, so with the default ``memory.write.reconcile.enabled: false`` (or no
    store) NOTHING is written and behaviour is unchanged.

    When enabled, salient candidates are extracted from the recent turn text and
    run through the reconcile pipeline, which writes durable facts to the
    holographic plane out-of-band. Every step is fail-soft: a bad candidate, a
    store error, or a missing handle never raises into the caller (the turn /
    background cycle keeps running).

    Designed to be called from a BACKGROUND daemon thread (see
    :func:`spawn_reconcile_turn`); it does not itself spawn a thread, so tests
    can drive it synchronously and assert the write.
    """
    try:
        if not config or not config.get("enabled", False):
            return []
        if store is None:
            return []
        candidates = _extract_candidates(user_text, response_text)
        if not candidates:
            return []
        return reconcile_now(
            store,
            candidates,
            source_store=source_store,
            model=model,
        )
    except Exception as exc:  # noqa: BLE001 - background; never surface
        logger.debug("reconcile worker: turn reconcile skipped (fail-soft): %s", exc)
        return []


def spawn_reconcile_turn(
    *,
    store: Any,
    config: Optional[dict],
    user_text: str = "",
    response_text: str = "",
    source_store: str = "orchestrator/self",
    model: Any = None,
) -> Optional[threading.Thread]:
    """Fire-and-forget a turn reconcile on a daemon thread. Never blocks.

    Returns the started thread (so a caller / test can ``join`` it), or ``None``
    when the gate is closed / no store is present (nothing to do). A non-blocking
    lock means an overlapping trigger while one pass is in flight is skipped
    rather than queued, mirroring the dreaming runner's single-flight worker.
    """
    if not config or not config.get("enabled", False) or store is None:
        return None

    def _worker() -> None:
        if not _reconcile_lock.acquire(blocking=False):
            return  # a reconcile pass is already running; skip this trigger
        try:
            maybe_reconcile_turn(
                store=store,
                config=config,
                user_text=user_text,
                response_text=response_text,
                source_store=source_store,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 - background; never surface
            logger.debug("reconcile worker: background pass error: %s", exc)
        finally:
            _reconcile_lock.release()

    t = threading.Thread(target=_worker, name="memory-reconcile", daemon=True)
    t.start()
    return t


__all__ = [
    "maybe_reconcile_turn",
    "reconcile_now",
    "spawn_reconcile_turn",
]
