"""Hook callbacks for the outcomes plugin — thin glue over the engine.

Kept separate from ``__init__`` so the dispatch logic is unit-testable with a fake
engine (no PluginManager). Wiring (verified against the real emit sites):

* ``post_tool_call`` — kwargs include ``session_id``, ``status``, ``error_type``;
  → ``engine.record_tool(session_id, success=...)``.
* ``post_llm_call`` — fires once per turn with ``session_id``, ``user_message`` (the
  message that *started* this turn = feedback on the prior turn), ``assistant_response``,
  ``turn_id``; → resolve the prior staged turn with that feedback, then stage this turn.
* ``on_session_end`` — flush the last staged turn (no feedback).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("hermes.plugins.outcomes.hooks")

_FAILURE_STATUSES = {
    "error", "failed", "failure", "denied", "blocked", "cancelled", "canceled",
    "timeout", "timed_out", "skipped", "incomplete", "aborted",
}


def tool_call_succeeded(*, status=None, error_type=None) -> bool:  # noqa: ANN001
    """True when a tool call looks successful (no error_type, non-failure status)."""
    if error_type:
        return False
    if status and str(status).strip().lower() in _FAILURE_STATUSES:
        return False
    return True


def make_post_tool_call(engine):  # noqa: ANN001, ANN201
    """``post_tool_call`` callback: accumulate the tool outcome onto the live turn."""

    def _cb(**kwargs) -> None:  # noqa: ANN003
        try:
            session_id = str(kwargs.get("session_id") or "")
            if not session_id:
                return
            success = tool_call_succeeded(
                status=kwargs.get("status"), error_type=kwargs.get("error_type")
            )
            engine.record_tool(session_id, success=success)
        except Exception as exc:  # noqa: BLE001 — hooks are fail-open
            logger.debug("outcomes: post_tool_call glue failed (%s)", exc)

    return _cb


def make_post_llm_call(engine, *, judge_fn=None):  # noqa: ANN001, ANN201
    """``post_llm_call`` callback: resolve the prior staged turn (with this message as
    its feedback), then stage the just-finished turn."""

    def _cb(**kwargs) -> None:  # noqa: ANN003
        try:
            session_id = str(kwargs.get("session_id") or "")
            if not session_id:
                return
            user_message = kwargs.get("user_message") or None
            turn_id = kwargs.get("turn_id") or kwargs.get("turn") or ""
            assistant_response = kwargs.get("assistant_response") or ""
            # 1) Score the prior staged turn using THIS message as its feedback.
            engine.resolve_pending(session_id, user_followup=user_message, judge_fn=judge_fn)
            # 2) Stage the turn that just finished (scored when the next message arrives).
            #    The trajectory carries BOTH sides so the batch judge can tell whether the
            #    response actually answered the request (a response alone is unjudgeable).
            engine.stage_turn(
                session_id, turn_id,
                trajectory_summary=_build_trajectory(user_message, assistant_response),
            )
        except Exception as exc:  # noqa: BLE001 — hooks are fail-open
            logger.debug("outcomes: post_llm_call glue failed (%s)", exc)

    return _cb


def make_on_session_end(engine, *, judge_fn=None):  # noqa: ANN001, ANN201
    """``on_session_end`` callback: flush the last staged turn (no feedback)."""

    def _cb(**kwargs) -> None:  # noqa: ANN003
        try:
            session_id = str(kwargs.get("session_id") or "")
            if not session_id:
                return
            engine.flush_pending(session_id, judge_fn=judge_fn)
        except Exception as exc:  # noqa: BLE001 — hooks are fail-open
            logger.debug("outcomes: on_session_end glue failed (%s)", exc)

    return _cb


def _summarize(text: str, *, limit: int = 600) -> str:
    """A short, deterministic trajectory summary for the judge."""
    text = " ".join((text or "").split())
    return text[:limit]


def _build_trajectory(user_message, assistant_response, *, limit: int = 700) -> str:  # noqa: ANN001
    """Build a judge-usable trajectory carrying both the request and the response.

    A bare assistant response is unjudgeable (the judge can't tell what was asked); pairing
    it with the user's message lets the judge assess whether the turn served the request.
    """
    user = _summarize(user_message or "", limit=300)
    asst = _summarize(assistant_response or "", limit=limit)
    parts = []
    if user:
        parts.append(f"User asked: {user}")
    parts.append(f"Assistant did/said: {asst}")
    return "\n".join(parts)
