"""Auxiliary-LLM adapters for the dreaming pipeline.

All LLM work routes through v2's auxiliary client under the ``dreaming`` task key
(``get_async_text_auxiliary_client("dreaming")``), so users can pin a cheap model
for consolidation independently of their chat model via ``auxiliary.dreaming`` in
config.yaml. The plugin registers that task in ``__init__.register``.

Three text tasks (ported/adapted from v1's prompts) plus an offline diversity
embedder:

- :func:`extract_facts`   — distil durable, user-specific facts from a session digest.
- :func:`score_fact`      — judge a fact's importance/durability in ``[0, 1]``.
- :func:`decide_supersede`— UPDATE / ADD / NOOP when a fact is near-duplicate.
- :func:`lexical_embed`   — term-frequency vectors for the diversity gate (no network).

If no auxiliary provider is configured, the text tasks degrade safely (extraction
yields nothing, scoring yields 0.0) so dreaming simply promotes nothing rather
than crashing.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Optional

logger = logging.getLogger("hermes.plugins.dreaming.llm")

_AUX_TASK = "dreaming"


class RateLimitedError(Exception):
    """Raised to signal the engine to halt the candidate loop and roll over.

    Named to match the engine's class-name detection (``engine.run_once``).
    """


_EXTRACT_SYSTEM = (
    "You curate an AI agent's long-term memory. Given a conversation transcript, "
    "extract any DURABLE facts ABOUT THE USER worth remembering across future "
    "sessions: their stable preferences, ongoing projects, identity/role, recurring "
    "constraints, important relationships, or decisions.\n\n"
    "STRICT RULES:\n"
    "- Every fact MUST be about the USER (their life, work, preferences, people, "
    "or things they own/use). A fact you could not phrase as 'The user …' is NOT "
    "a user fact — drop it.\n"
    "- NEVER extract general world knowledge, encyclopedic facts, or content the "
    "assistant merely EXPLAINED in an answer (e.g. company profiles, model/benchmark "
    "comparisons, definitions, news). Those are answer content, not facts about the user.\n"
    "- IGNORE ephemeral chatter, one-off task details, transient state, document "
    "structure (headings, tables, 'Bottom line:' summaries), and anything the agent "
    "said about itself.\n\n"
    "Output ONE fact per line, each a concise third-person statement about the user "
    "(e.g. 'Prefers TypeScript over JavaScript for new projects.'). If there is "
    "nothing durable about the USER, output exactly: NONE"
)

_SCORE_SYSTEM = (
    "You judge whether a candidate fact is worth storing in an AI agent's "
    "permanent memory. Consider durability (will it still be true/useful in a "
    "month?), specificity, and usefulness. Respond with ONLY a number between "
    "0.0 and 1.0 — 1.0 = definitely store, 0.0 = ephemeral/useless."
)

_DECISION_SYSTEM = (
    "An AI agent's memory already contains an EXISTING entry. A NEW candidate fact "
    "is highly similar. Decide the relationship and respond with ONE word:\n"
    "UPDATE — the new fact corrects/refines/supersedes the existing one (replace it).\n"
    "ADD — they are distinct facts that should coexist.\n"
    "NOOP — the new fact is a duplicate; discard it."
)


async def _aux_chat(system: str, user: str, *, max_tokens: int, temperature: float = 0.0) -> Optional[str]:
    """One auxiliary chat completion. Returns text, or None if unavailable."""
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client
    except Exception as exc:  # noqa: BLE001 — core not importable (standalone tests)
        logger.debug("dreaming: auxiliary client unavailable (%s)", exc)
        return None

    client, model = get_async_text_auxiliary_client(_AUX_TASK)
    if client is None or not model:
        logger.debug("dreaming: no auxiliary provider configured for task %r", _AUX_TASK)
        return None

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001
        if _looks_rate_limited(exc):
            raise RateLimitedError(str(exc)) from exc
        logger.warning("dreaming: aux chat failed: %s: %s", type(exc).__name__, exc)
        return None

    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError):
        return None


def _looks_rate_limited(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 429 or "429" in str(exc)


async def extract_facts(digest: str, *, max_facts: int = 5) -> list[str]:
    """Distil durable facts from a session digest. Empty list if none."""
    if not digest.strip():
        return []
    out = await _aux_chat(_EXTRACT_SYSTEM, digest, max_tokens=400)
    if not out or out.strip().upper() == "NONE":
        return []
    facts: list[str] = []
    for line in out.splitlines():
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if not line or line.upper() == "NONE":
            continue
        facts.append(line)
        if len(facts) >= max_facts:
            break
    return facts


async def score_fact(text: str) -> float:
    """Importance score in [0, 1]. 0.0 when no provider or unparseable."""
    out = await _aux_chat(_SCORE_SYSTEM, text, max_tokens=8)
    if not out:
        return 0.0
    match = re.search(r"\d*\.?\d+", out)
    if not match:
        return 0.0
    try:
        return max(0.0, min(1.0, float(match.group())))
    except ValueError:
        return 0.0


async def decide_supersede(new_text: str, existing: str) -> str:
    """Return UPDATE / ADD / NOOP. Defaults to NOOP when unavailable."""
    user = f"EXISTING:\n{existing}\n\nNEW:\n{new_text}"
    out = await _aux_chat(_DECISION_SYSTEM, user, max_tokens=4)
    if not out:
        return "NOOP"
    m = re.search(r"\b(UPDATE|ADD|NOOP)\b", out.upper())
    return m.group(1) if m else "NOOP"


# ---------------------------------------------------------------------------
# Offline diversity embedder — term-frequency cosine over the batch vocabulary.
# Real semantic embeddings would be better; this is a deterministic,
# zero-dependency stand-in that reliably catches near-duplicate promotions.
# ---------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-.]{1,}")


async def lexical_embed(texts: list[str]) -> list[list[float]]:
    """Aligned term-frequency vectors over the union vocabulary of *texts*."""
    tokenized = [_TOKEN_RE.findall(t.lower()) for t in texts]
    vocab: dict[str, int] = {}
    for toks in tokenized:
        for tok in toks:
            if tok not in vocab:
                vocab[tok] = len(vocab)
    dim = len(vocab)
    vectors: list[list[float]] = []
    for toks in tokenized:
        vec = [0.0] * dim
        for tok in toks:
            vec[vocab[tok]] += 1.0
        # L2-normalise so cosine is just the dot product (and lengths don't skew).
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        vectors.append(vec)
    return vectors


def aux_client_available() -> bool:
    """True if an auxiliary provider is configured for the dreaming task."""
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client

        client, model = get_async_text_auxiliary_client(_AUX_TASK)
        return client is not None and bool(model)
    except Exception:  # noqa: BLE001
        return False


def _embed_model() -> str:
    """The embeddings model for the diversity gate, from config (opt-in).

    Empty string disables semantic embeddings (lexical fallback is used). Read from
    ``dreaming.embed_model`` in config.yaml; no behaviour change unless configured.
    """
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        block = cfg.get("dreaming", {})
        if isinstance(block, dict):
            return str(block.get("embed_model", "") or "").strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


async def semantic_embed(texts: list[str]) -> list[list[float]]:
    """Semantic embeddings via the auxiliary provider's ``/embeddings`` endpoint.

    Opt-in: only used when ``dreaming.embed_model`` is configured AND the provider
    exposes an embeddings endpoint. Falls back to :func:`lexical_embed` on any failure,
    absence, or shape mismatch — so the diversity gate never crashes and stays
    deterministic offline.
    """
    model = _embed_model()
    if not model or not texts:
        return await lexical_embed(texts)
    try:
        from agent.auxiliary_client import get_async_text_auxiliary_client

        client, _chat_model = get_async_text_auxiliary_client(_AUX_TASK)
        if client is None:
            return await lexical_embed(texts)
        resp = await client.embeddings.create(model=model, input=list(texts))
        vectors = [list(item.embedding) for item in resp.data]
        if len(vectors) == len(texts) and all(vectors):
            return vectors
    except Exception as exc:  # noqa: BLE001
        logger.debug("dreaming: semantic embeddings unavailable (%s); using lexical", exc)
    return await lexical_embed(texts)
