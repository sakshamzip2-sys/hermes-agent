"""Auxiliary-LLM helpers for proactivity sources (commitment extraction, etc.).

Routes through v2's auxiliary client under the ``proactivity`` task key so users can
pin a cheap model via ``auxiliary.proactivity`` in config.yaml. Degrades safely: with
no provider configured, extractors return nothing rather than crashing.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger("hermes.plugins.proactivity.llm")

_AUX_TASK = "proactivity"

_COMMITMENT_SYSTEM = (
    "You read a user's recent messages to an AI assistant and extract COMMITMENTS the "
    "user made — things they said THEY would do, or explicitly asked to be reminded "
    "about. Examples: 'I'll email Sam on Friday', 'I need to finish the deck by Tuesday', "
    "'remind me to call the bank'. Do NOT extract things the assistant should do, "
    "questions, or idle chatter. For each commitment output a compact JSON object on its "
    "own line: {\"what\": \"<concise third-person statement of the commitment>\", "
    "\"due\": \"<natural-language due time if stated, else empty>\", "
    "\"asked_reminder\": <true if the user explicitly asked to be reminded, else false>}. "
    "Output ONLY those JSON lines, at most 5, newest/most-important first. If there are "
    "no genuine commitments, output exactly: NONE"
)


async def _aux_chat(system: str, user: str, *, max_tokens: int, temperature: float = 0.0) -> Optional[str]:
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client
    except Exception as exc:  # noqa: BLE001 — core not importable (standalone tests)
        logger.debug("proactivity: auxiliary client unavailable (%s)", exc)
        return None
    client, model = get_async_text_auxiliary_client(_AUX_TASK)
    if client is None or not model:
        return None
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("proactivity: aux chat failed: %s: %s", type(exc).__name__, exc)
        return None


async def extract_commitments(digest: str, *, max_items: int = 5) -> list[dict]:
    """Return [{what, due, asked_reminder}] commitments from recent user messages."""
    if not digest.strip():
        return []
    out = await _aux_chat(_COMMITMENT_SYSTEM, digest, max_tokens=400)
    if not out or out.strip().upper() == "NONE":
        return []
    items: list[dict] = []
    for line in out.splitlines():
        line = line.strip().strip(",")
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        what = str(obj.get("what", "")).strip()
        if not what:
            continue
        items.append({
            "what": what,
            "due": str(obj.get("due", "")).strip(),
            "asked_reminder": bool(obj.get("asked_reminder", False)),
        })
        if len(items) >= max_items:
            break
    return items


def aux_available() -> bool:
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client

        client, model = get_async_text_auxiliary_client(_AUX_TASK)
        return client is not None and bool(model)
    except Exception:  # noqa: BLE001
        return False


# Lightweight regex pre-filter so we only pay for an LLM call when the window plausibly
# contains a commitment — keeps polling cheap.
_COMMIT_HINT = re.compile(
    r"\b(i'?ll|i will|i'?m going to|i need to|i have to|i should|remind me|"
    r"don'?t let me forget|i promised|by (tomorrow|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|next week|tonight|eod|end of day))\b",
    re.IGNORECASE,
)


def has_commitment_hint(text: str) -> bool:
    return bool(_COMMIT_HINT.search(text or ""))
