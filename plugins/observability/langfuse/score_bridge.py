"""Outcome-to-trace score bridge (Part 2, Slice 2 - the P2-2 join).

The turn_score evaluator (``plugins/outcomes``) persists fused per-turn scores to a
local SQLite ledger but never attaches the verdict to the Langfuse trace. This module
is the thin, push-only, fail-open join: it reads the scored rows for a session and calls
``client.create_score`` on the trace minted with the SAME deterministic seed the per-turn
root trace used (``trace_id_seed`` in the plugin ``__init__``).

Design invariants (per PART2-gap-map-and-plan.md, Slice 2):
* No new scorer. The local ``turn_outcomes`` DB stays the source of truth.
* Push-only. The bridge never mutates the ledger.
* Fail-open. A Langfuse error (or a missing DB) is swallowed and never breaks the flush.
* DEFAULT-OFF. The single caller (``on_session_end_score_bridge``) is gated on
  ``observability.langfuse.score_bridge`` before this module is even imported.

The Langfuse SDK contract used here (verified against the v3 Python SDK):
``client.create_score(trace_id=..., name=..., value=..., data_type="NUMERIC", comment=...)``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Score name the verdict lands under on the trace. Stable so dashboards/eval
# queries can filter on a single key.
SCORE_NAME = "turn_score"


def _trace_id_for(client: Any, session_id: str, task: str) -> Optional[str]:
    """Reconstruct the trace_id for one (session, task) using the shared seed.

    Imports the seed helper lazily from the plugin package so this module has no
    import-time dependency on the plugin's module-level state.
    """
    try:
        from . import trace_id_seed

        return client.create_trace_id(seed=trace_id_seed(session_id, task))
    except Exception as exc:  # noqa: BLE001 - fail-open
        logger.debug("score bridge: trace_id reconstruction failed (%s)", exc)
        return None


def _emit_score(
    client: Any, *, trace_id: str, value: float, comment: Optional[str] = None
) -> bool:
    """Call ``create_score`` on one trace. Returns True on success, False on any error."""
    try:
        client.create_score(
            trace_id=trace_id,
            name=SCORE_NAME,
            value=float(value),
            data_type="NUMERIC",
            comment=comment,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - fail-open, one bad score never aborts the flush
        logger.debug("score bridge: create_score failed for %s (%s)", trace_id, exc)
        return False


def flush_session_scores(
    client: Any,
    *,
    session_id: str,
    task: str,
    db_path: Any = None,
    limit: int = 150,
) -> int:
    """Push every scored turn of one session onto its Langfuse trace.

    Reads the session's scored rows from the outcomes ledger (read-only) and emits one
    ``create_score`` per turn on the trace minted from the (session, task) seed. The
    gateway sets task == session_id for a turn, so callers pass ``task=session_id`` unless
    a distinct task identifier is known. Returns the number of scores successfully emitted.

    Fully fail-open: a missing DB yields zero rows (no scores), and any per-row Langfuse
    error is swallowed so the rest of the session still flushes.
    """
    if client is None or not session_id:
        return 0

    try:
        from plugins.outcomes.store import session_turn_rows
    except Exception as exc:  # noqa: BLE001 - outcomes plugin absent / import error
        logger.debug("score bridge: outcomes store unavailable (%s)", exc)
        return 0

    try:
        rows = session_turn_rows(session_id, limit=limit, db_path=db_path)
    except Exception as exc:  # noqa: BLE001 - fail-open on any read error
        logger.debug("score bridge: session rows read failed (%s)", exc)
        return 0

    if not rows:
        return 0

    trace_id = _trace_id_for(client, session_id, task)
    if trace_id is None:
        return 0

    emitted = 0
    for row in rows:
        score = row.get("turn_score")
        if score is None:
            continue
        comment = None
        turn = row.get("turn")
        if turn is not None:
            comment = f"turn={turn}"
        if _emit_score(client, trace_id=trace_id, value=score, comment=comment):
            emitted += 1

    if emitted:
        try:
            client.flush()
        except Exception as exc:  # noqa: BLE001 - fail-open
            logger.debug("score bridge: flush failed (%s)", exc)

    return emitted
