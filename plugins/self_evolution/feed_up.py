"""ENRICH ↑ : feed local turn-outcomes UP to GBrain (the upward arrow of the flywheel).

The cross-engine consume direction (Honcho/GBrain dreams → local memory) already works via
``dream_orchestrator``. This is the missing UPWARD arrow: push "which sessions mattered"
(per-session mean turn_score, from the outcomes store) into GBrain as a page, so GBrain's
autopilot consolidation can weight high-signal sessions and surface them on recall. That
makes the external memory engines smarter about the user *because* of how turns actually went.

Reuses the orchestrator's proven GBrain RPC (SSE-aware). Gated + default-OFF + fail-soft:
when GBrain is down or no token is set, it no-ops rather than disturbing the cycle.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("hermes.plugins.self_evolution.feed_up")

_SIGNAL_SLUG = "agent-self-evolution-signal"


def build_signal_page(session_scores: list, *, top: int = 20) -> tuple[str, str]:
    """Render the (slug, markdown) GBrain page from per-session mean turn_scores.

    ``session_scores`` is a list of (session_id, mean_score) newest-first.
    """
    rows = list(session_scores)[:top]
    lines = [
        "---",
        f"title: {_SIGNAL_SLUG}",
        "tags: [self-evolution, outcomes, agent-signal]",
        "---",
        "",
        "# Agent self-evolution signal",
        "",
        "_Per-session quality scores (turn_score mean) the agent recorded. Higher = the "
        "session went well; lower = friction/corrections. Use this to weight which sessions "
        "and topics to consolidate and surface._",
        "",
        "## Sessions by outcome",
        "",
    ]
    if not rows:
        lines.append("_(no scored sessions yet)_")
    for sid, mean in rows:
        try:
            band = "good" if mean >= 0.6 else ("poor" if mean < 0.45 else "mixed")
        except TypeError:
            band = "unknown"
        lines.append(f"- `{sid}` — mean turn_score **{mean:.3f}** ({band})")
    lines.append("")
    return _SIGNAL_SLUG, "\n".join(lines)


def feed_up(
    session_scores: list,
    *,
    rpc_fn: Optional[Callable[..., dict]] = None,
    token: Optional[str] = None,
    timeout: float = 30.0,
) -> dict:
    """Write the outcome-signal page to GBrain via put_page. Fail-soft.

    ``rpc_fn(method, params, *, token, timeout) -> dict`` is injectable (defaults to the
    orchestrator's GBrain RPC). Returns a summary dict; never raises.
    """
    if not session_scores:
        return {"ok": True, "skipped": "no scored sessions"}

    slug, markdown = build_signal_page(session_scores)
    try:
        if rpc_fn is None or token is None:
            from plugins.dream_orchestrator.targets import _gbrain_rpc, _gbrain_token

            rpc_fn = rpc_fn or _gbrain_rpc
            token = token or _gbrain_token()
        if not token:
            return {"ok": True, "skipped": "GBRAIN_MCP_TOKEN not set"}
        resp = rpc_fn(
            "tools/call",
            {"name": "put_page", "arguments": {"slug": slug, "content": markdown}},
            token=token, timeout=timeout,
        )
        if isinstance(resp, dict) and resp.get("error"):
            return {"ok": False, "error": str(resp["error"])[:200]}
        return {"ok": True, "fed": len(session_scores), "slug": slug}
    except Exception as exc:  # noqa: BLE001 — UP feed must never break the cycle
        logger.debug("feed_up: GBrain push failed (%s)", exc)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
