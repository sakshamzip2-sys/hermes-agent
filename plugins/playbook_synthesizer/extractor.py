"""Extract PlaybookCandidates from recent dreaming output (the DREAM→EVOLVE feed).

After a dream cycle promotes facts, this asks the auxiliary LLM: "is there a reusable,
multi-step PROCEDURE the agent keeps doing here worth capturing as a skill?" The model
returns zero or more structured candidates. Model-agnostic (aux-client seam, injectable
``chat_fn``) and fail-soft: no provider / unparseable / nothing-found → empty list, so the
self-evolution cycle simply synthesizes nothing rather than crashing.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Optional

from .synthesizer import PlaybookCandidate

logger = logging.getLogger("hermes.plugins.playbook_synthesizer.extractor")

ChatFn = Callable[..., Awaitable[Optional[str]]]

_SYSTEM = (
    "You curate an AI agent's reusable skills. Given recent durable facts/observations the "
    "agent learned, identify any REUSABLE MULTI-STEP PROCEDURE worth saving as a skill — a "
    "workflow, a fix, or a pitfall-avoidance the agent will face again. Ignore one-off facts "
    "and simple preferences (those belong in memory, not skills). "
    "Respond with a JSON array (possibly empty). Each item: "
    '{"name": "<short imperative title>", "description": "<when to use, one sentence>", '
    '"steps": ["step 1", "step 2", ...], "recurrence": <int observations>}. '
    "Only include procedures with at least 2 steps. Output ONLY the JSON array."
)


async def extract_candidates(
    facts: list[str],
    *,
    chat_fn: Optional[ChatFn] = None,
    min_facts: int = 1,
) -> list[PlaybookCandidate]:
    """Return PlaybookCandidates distilled from ``facts``. Empty on any failure/absence."""
    facts = [f for f in (facts or []) if f and f.strip()]
    if len(facts) < min_facts:
        return []
    fn = chat_fn or _default_chat_fn
    user = "Recent learned facts:\n" + "\n".join(f"- {f}" for f in facts[:50])
    try:
        text = await fn(_SYSTEM, user, max_tokens=800)
    except Exception as exc:  # noqa: BLE001
        logger.debug("playbook extractor: chat failed (%s)", exc)
        return []
    return _parse_candidates(text)


def _parse_candidates(text: Optional[str]) -> list[PlaybookCandidate]:
    if not text:
        return []
    # Tolerate prose around the JSON array.
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[PlaybookCandidate] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "") or "").strip()
        steps = [str(s).strip() for s in item.get("steps", []) if str(s).strip()]
        if not name or len(steps) < 2:
            continue
        try:
            recurrence = int(item.get("recurrence", 1))
        except (TypeError, ValueError):
            recurrence = 1
        out.append(PlaybookCandidate(
            name=name,
            description=str(item.get("description", "") or name).strip(),
            steps=steps,
            evidence=[str(e).strip() for e in item.get("evidence", []) if str(e).strip()],
            recurrence=max(1, recurrence),
        ))
    return out


async def _default_chat_fn(system: str, user: str, *, max_tokens: int) -> Optional[str]:
    """Route through the model-agnostic auxiliary client (fail-soft → None)."""
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client
    except Exception:  # noqa: BLE001
        return None
    client, model = get_async_text_auxiliary_client("playbook_synthesizer")
    if client is None or not model:
        return None
    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("playbook extractor: aux chat failed (%s)", exc)
        return None
