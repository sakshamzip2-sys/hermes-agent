"""Channels — inject out-of-band events into the live agent loop (model-agnostic).

Ports the Claude Code "channels" concept: an external source (an MCP server the
agent spawned, a plugin, a monitor, a webhook bridge) PUSHES an event into the
running conversation so the model reacts to it on the next turn — delivered
wrapped as ``<channel source="..." key="value">body</channel>``.

Rather than build new delivery machinery, channels reuse the path background
processes already use to surface output to the agent: the process-registry
``completion_queue`` drained by the gateway after each turn
(``gateway/run.py:_drain_gateway_watch_events`` ->
``_format_gateway_process_notification``).  An in-process source calls
:func:`inject_channel_event`; the gateway drains the event and injects it on the
next turn (events queue and are delivered in order — exactly the channel
semantic: several events arriving while the agent is busy are delivered together
on the next turn).

Model-agnostic: this is pure data on a queue plus string formatting — no
provider/model coupling anywhere.  Security note (SECURITY.md): an ungated
channel is a prompt-injection vector; callers that bridge an external/untrusted
sender MUST gate on the sender's identity before calling ``inject_channel_event``
(the v2 trust boundary is OS/VM-level, and channel content is untrusted input).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Channel meta keys become tag attributes; restrict to identifier-safe keys so a
# crafted key can't break out of the tag (hyphenated/odd keys are dropped, same
# rule Claude Code applies).
_ATTR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CHANNEL_EVENT_TYPE = "channel"


def _safe_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(meta, dict):
        return out
    for k, v in meta.items():
        ks = str(k)
        if _ATTR_KEY_RE.match(ks):
            # Strip characters that could break the attribute quoting.
            out[ks] = str(v).replace('"', "'").replace("\n", " ")
    return out


def inject_channel_event(
    source: str,
    content: str,
    meta: Optional[Dict[str, Any]] = None,
    *,
    platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    chat_type: Optional[str] = None,
) -> bool:
    """Push a channel event onto the shared completion queue for the live loop.

    Routing: by default the event is delivered to the gateway's configured home
    channel (the natural destination for out-of-band notifications).  Pass
    ``platform`` + ``chat_id`` (+ optional ``chat_type``) to target a specific
    conversation instead — those are read by the gateway's source resolver and
    take precedence over the home-channel fallback.

    Returns True when the event was enqueued.  Never raises — a channel that
    cannot deliver must not crash its caller (mirrors the fire-and-forget
    notification contract).
    """
    if not str(content or "").strip():
        return False
    try:
        from tools.process_registry import process_registry
    except Exception as exc:  # noqa: BLE001
        logger.debug("channels: process registry unavailable: %s", exc)
        return False

    evt = {
        "type": CHANNEL_EVENT_TYPE,
        "source": str(source or "channel"),
        "content": str(content),
        "meta": _safe_meta(meta),
    }
    # Optional explicit routing (targeted delivery).  Read by the gateway's
    # _build_process_event_source via evt.get("platform")/chat_id/chat_type.
    if platform:
        evt["platform"] = str(platform).strip().lower()
    if chat_id:
        evt["chat_id"] = str(chat_id).strip()
    if chat_type:
        evt["chat_type"] = str(chat_type).strip().lower()
    try:
        process_registry.completion_queue.put(evt)
        logger.info("channels: injected event from source=%s", evt["source"])
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("channels: failed to enqueue event from %s: %s", source, exc)
        return False


def format_channel_event(evt: dict) -> str:
    """Render a channel event as ``<channel source=... attrs>content</channel>``.

    Used by the gateway watch-event drain to inject the event into the
    conversation as a user-visible block the model can read and act on.
    """
    source = str(evt.get("source") or "channel")
    content = str(evt.get("content") or "")
    meta = evt.get("meta") or {}
    attrs = f' source="{source}"'
    for k, v in _safe_meta(meta).items():
        attrs += f' {k}="{v}"'
    return f"<channel{attrs}>\n{content}\n</channel>"
