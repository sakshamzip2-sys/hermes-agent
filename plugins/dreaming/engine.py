"""Dreaming engine — three-gate consolidation of episodic activity into MEMORY.md.

Ported from OpenComputer v1 (``opencomputer/agent/evolution/dreaming.py``) into the
v2 (hermes-agent) idiom as a self-contained plugin engine. The v1 module imported
``plugin_sdk.embeddings``; here the embedding contract is expressed as a plain
injectable callable so the engine has ZERO host dependencies and is fully testable
in isolation.

The pipeline promotes high-signal episodic facts (extracted from recent sessions)
into the user's declarative ``MEMORY.md`` so future turns can recall them. Each
candidate passes three gates:

1. **Score gate** — an auxiliary LLM judges importance/durability in ``[0, 1]``.
   Below ``score_threshold`` (default 0.65) the fact is not promoted.
2. **Recall gate** — how many times the user/agent came back to this fact across
   sessions. Below ``min_recall_count`` (default 2) suggests it was a one-off.
   v2 has no ``recall_citations`` table, so the host injects a proxy
   (FTS over session history) or disables the gate; see ``runner.py``.
3. **Diversity gate** — cosine similarity to the nearest existing MEMORY.md entry.
   Above ``diversity_threshold`` (default 0.8) means it's effectively a duplicate.

Routing (identical to v1):

- All three gates pass               → PROMOTE to MEMORY.md (capped per run).
- Passed diversity, failed score OR  → HELD in DREAMS.md (lower-confidence pen).
  recall
- Failed diversity                   → DROP (or SUPERSEDE an existing entry when a
                                       decision_fn is wired and judges it a
                                       contradiction/refinement rather than a dup).

The engine is the *pure core*. Candidate generation, MEMORY.md/DREAMS.md I/O, the
auxiliary-LLM calls, persistence of processed ids, and cron/CLI wiring all live in
sibling modules that inject their behaviour as callables.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("hermes.plugins.dreaming.engine")

# ---------------------------------------------------------------------------
# Injectable callable contracts (documented as type aliases for readability)
# ---------------------------------------------------------------------------
# score_fn(text) -> float in [0, 1]                       (async)
# recall_count_fn(event_id) -> int                        (sync)
# embed_fn(list[str]) -> list[list[float]]                (async, optional)
# promote_fn(text) -> None       append to MEMORY.md      (sync)
# hold_fn(text, max_bytes) -> None  append to DREAMS.md   (sync)
# decision_fn(new_text, existing) -> "UPDATE"|"ADD"|"NOOP" (async, optional; SUPERSEDE)
# replace_fn(old_text, new_text) -> bool  in-place MEMORY.md replace (sync, optional)
ScoreFn = Callable[[str], Awaitable[float]]
RecallCountFn = Callable[[str], int]
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]
PromoteFn = Callable[[str], None]
HoldFn = Callable[[str, int], None]
DecisionFn = Callable[[str, str], Awaitable[str]]
ReplaceFn = Callable[[str, str], bool]


class DreamOutcome(Enum):
    """Routing decision for a single candidate event."""

    PROMOTED = "promoted"  # passed all three gates -> MEMORY.md
    HELD = "held"          # failed score or recall but passed diversity -> DREAMS.md
    DROPPED = "dropped"    # failed diversity (and not a supersede) -> drop + audit
    UPDATED = "updated"    # high-similarity contradiction -> replaced an existing entry


@dataclass(frozen=True)
class DreamCandidate:
    """One episodic fact under evaluation.

    ``event_id`` is a stable hash used for idempotency; ``raw_text`` is the prose
    that would become a MEMORY.md entry — the gates score this directly.
    """

    event_id: str
    raw_text: str
    timestamp_ns: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DreamGateResult:
    """Per-candidate outcome with full rationale for audit."""

    candidate: DreamCandidate
    outcome: DreamOutcome
    score: float
    recall_count: int
    diversity_score: float  # cosine to nearest existing memory; 0.0 = no embeddings
    rationale: str
    old_text: Optional[str] = None  # for UPDATED: the entry that was replaced


@dataclass(frozen=True)
class DreamRunSummary:
    """Aggregate result of one dreaming pass."""

    promoted: tuple[DreamGateResult, ...] = ()
    held: tuple[DreamGateResult, ...] = ()
    dropped: tuple[DreamGateResult, ...] = ()
    updated: tuple[DreamGateResult, ...] = ()
    skipped_already_processed: int = 0
    total_evaluated: int = 0
    rate_limited: bool = False

    def counts(self) -> dict[str, int]:
        return {
            "promoted": len(self.promoted),
            "held": len(self.held),
            "dropped": len(self.dropped),
            "updated": len(self.updated),
            "skipped": self.skipped_already_processed,
            "evaluated": self.total_evaluated,
        }


@dataclass
class DreamingConfig:
    """Tunable thresholds. Defaults match v1 exactly."""

    enabled: bool = True
    score_threshold: float = 0.65
    min_recall_count: int = 2
    diversity_threshold: float = 0.8
    max_promotions_per_run: int = 20
    dreams_md_max_bytes: int = 16384
    candidate_fetch_limit: int = 50
    supersede_enabled: bool = True
    recall_gate_enabled: bool = True
    """v2-specific: when False the recall gate always passes. v2 lacks v1's
    ``recall_citations`` table; hosts without a recall proxy disable this gate."""


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. 0.0 on degenerate input."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


async def best_match_against(
    text: str,
    existing: list[str],
    *,
    embed_fn: Optional[EmbedFn],
) -> tuple[float, int]:
    """Return (max_cosine, best_index) of *text* against *existing* entries.

    When no ``embed_fn`` is available (or it raises), returns ``(0.0, -1)`` —
    every candidate then looks novel, biasing toward over-promotion rather than
    silently dropping facts (v1 parity).
    """
    if not existing or embed_fn is None:
        return (0.0, -1)
    try:
        vectors = await embed_fn([text, *existing])
    except Exception as exc:  # noqa: BLE001 — degrade to "novel" on any embed failure
        logger.warning(
            "dreaming: embed_fn raised %s: %s; treating candidate as novel",
            type(exc).__name__,
            exc,
        )
        return (0.0, -1)
    if not vectors or len(vectors) < 2:
        return (0.0, -1)
    cand_vec = vectors[0]
    best_cos = 0.0
    best_idx = -1
    for i, vec in enumerate(vectors[1:]):
        cos = _cosine(cand_vec, vec)
        if cos > best_cos:
            best_cos = cos
            best_idx = i
    return (best_cos, best_idx)


class DreamingPipeline:
    """The pure three-gate engine. Inject behaviour; call :meth:`run_once`."""

    def __init__(
        self,
        config: DreamingConfig,
        *,
        score_fn: ScoreFn,
        recall_count_fn: RecallCountFn,
        promote_fn: PromoteFn,
        hold_fn: HoldFn,
        embed_fn: Optional[EmbedFn] = None,
        decision_fn: Optional[DecisionFn] = None,
        replace_fn: Optional[ReplaceFn] = None,
    ) -> None:
        self.config = config
        self.score_fn = score_fn
        self.recall_count_fn = recall_count_fn
        self.promote_fn = promote_fn
        self.hold_fn = hold_fn
        self.embed_fn = embed_fn
        self.decision_fn = decision_fn
        self.replace_fn = replace_fn

    async def run_once(
        self,
        candidates: list[DreamCandidate],
        *,
        existing_memories: list[str],
        already_processed_event_ids: Optional[set[str]] = None,
    ) -> DreamRunSummary:
        """Evaluate candidates through the three gates and route each.

        Idempotent: candidates whose ``event_id`` is in
        ``already_processed_event_ids`` are skipped and counted.
        """
        if not self.config.enabled:
            logger.info("dreaming: disabled by config; skipping run")
            return DreamRunSummary()

        skip_set = already_processed_event_ids or set()
        promoted: list[DreamGateResult] = []
        held: list[DreamGateResult] = []
        dropped: list[DreamGateResult] = []
        updated: list[DreamGateResult] = []
        skipped = 0
        rate_limited = False

        for cand in candidates:
            if cand.event_id in skip_set:
                skipped += 1
                continue

            if len(promoted) >= self.config.max_promotions_per_run:
                # Promotion cap hit; remaining candidates roll over to the next
                # run. Don't even score them — saves auxiliary-LLM cost.
                break

            # --- Non-fact backstop -----------------------------------------
            # Assistant narration ("Let me now look at the TUI…"), tool-call
            # fragments and table rows must NEVER reach MEMORY.md. The digest
            # filter (candidates._is_noise_line) only covers fresh extraction;
            # held candidates re-promoted from DREAMS.md bypass it, so re-apply
            # it here as a deterministic gate that covers EVERY promotion path.
            from plugins.dreaming.candidates import _is_noise_line

            if _is_noise_line(cand.raw_text):
                dropped.append(
                    self._dropped(cand, 0.0, 0, 0.0, "non-fact (narration/tool fragment)")
                )
                continue

            # --- Score gate ------------------------------------------------
            try:
                score = float(await self.score_fn(cand.raw_text))
            except Exception as exc:  # noqa: BLE001
                # Halt the whole loop on rate-limit (detected by class name to
                # avoid importing a provider SDK on this hot path). Remaining
                # candidates roll over rather than each triggering a fresh 429.
                if type(exc).__name__ == "RateLimitedError":
                    logger.warning("dreaming: rate-limited; halting and rolling over")
                    rate_limited = True
                    break
                logger.warning(
                    "dreaming: score_fn raised %s: %s; treating as 0.0",
                    type(exc).__name__,
                    exc,
                )
                score = 0.0
            score = max(0.0, min(1.0, score))

            # --- Recall gate -----------------------------------------------
            if self.config.recall_gate_enabled:
                try:
                    recall_count = int(self.recall_count_fn(cand.event_id))
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "dreaming: recall_count_fn raised %s: %s; treating as 0",
                        type(exc).__name__,
                        exc,
                    )
                    recall_count = 0
                recall_count = max(0, recall_count)
                recall_ok = recall_count >= self.config.min_recall_count
            else:
                recall_count = self.config.min_recall_count  # report as "met"
                recall_ok = True

            # --- Diversity gate --------------------------------------------
            diversity, best_idx = await best_match_against(
                cand.raw_text, existing_memories, embed_fn=self.embed_fn
            )

            score_ok = score >= self.config.score_threshold
            diversity_ok = diversity < self.config.diversity_threshold

            # --- High-similarity adjudication (SUPERSEDE) ------------------
            if not diversity_ok:
                kind, result = await self._route_high_similarity(
                    cand,
                    existing_memories,
                    best_idx=best_idx,
                    score=score,
                    recall_count=recall_count,
                    diversity=diversity,
                )
                if kind == "UPDATE":
                    updated.append(result)
                    continue
                if kind == "DROP":
                    dropped.append(result)
                    continue
                # kind == "ADD": adjudicator judged it a distinct fact despite the
                # high cosine — fall through to normal routing as if diversity passed.
                diversity_ok = True

            # --- Promote / hold routing ------------------------------------
            if score_ok and recall_ok and diversity_ok:
                rationale = (
                    f"all gates passed: score={score:.2f}, "
                    f"recall={recall_count}, diversity={diversity:.3f}"
                )
                try:
                    self.promote_fn(cand.raw_text)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "dreaming: promote_fn raised %s: %s; downgrading to HELD",
                        type(exc).__name__,
                        exc,
                    )
                    held.append(self._held(cand, score, recall_count, diversity,
                                           f"promote failed ({type(exc).__name__})"))
                    continue
                promoted.append(
                    DreamGateResult(
                        candidate=cand,
                        outcome=DreamOutcome.PROMOTED,
                        score=score,
                        recall_count=recall_count,
                        diversity_score=diversity,
                        rationale=rationale,
                    )
                )
                continue

            # Passed diversity but failed score and/or recall -> DREAMS.md.
            why = []
            if not score_ok:
                why.append(f"score={score:.2f}<{self.config.score_threshold}")
            if not recall_ok:
                why.append(f"recall={recall_count}<{self.config.min_recall_count}")
            try:
                self.hold_fn(cand.raw_text, self.config.dreams_md_max_bytes)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dreaming: hold_fn raised %s: %s; dropping instead",
                    type(exc).__name__,
                    exc,
                )
                dropped.append(self._dropped(cand, score, recall_count, diversity,
                                             f"hold failed ({type(exc).__name__})"))
                continue
            held.append(self._held(cand, score, recall_count, diversity,
                                   "held: " + ", ".join(why)))

        return DreamRunSummary(
            promoted=tuple(promoted),
            held=tuple(held),
            dropped=tuple(dropped),
            updated=tuple(updated),
            skipped_already_processed=skipped,
            total_evaluated=len(candidates) - skipped,
            rate_limited=rate_limited,
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _held(cand, score, recall, diversity, why) -> DreamGateResult:
        return DreamGateResult(
            candidate=cand, outcome=DreamOutcome.HELD, score=score,
            recall_count=recall, diversity_score=diversity, rationale=why,
        )

    @staticmethod
    def _dropped(cand, score, recall, diversity, why) -> DreamGateResult:
        return DreamGateResult(
            candidate=cand, outcome=DreamOutcome.DROPPED, score=score,
            recall_count=recall, diversity_score=diversity, rationale=why,
        )

    async def _route_high_similarity(
        self,
        cand: DreamCandidate,
        existing_memories: list[str],
        *,
        best_idx: int,
        score: float,
        recall_count: int,
        diversity: float,
    ) -> tuple[str, DreamGateResult]:
        """Adjudicate a candidate that is too similar to an existing entry.

        Returns ``(kind, result)`` where kind is ``"UPDATE"``, ``"DROP"``, or
        ``"ADD"``. Without a ``decision_fn`` (or ``supersede_enabled=False``),
        the historical behaviour is a hard DROP.
        """
        existing_entry = (
            existing_memories[best_idx]
            if 0 <= best_idx < len(existing_memories)
            else ""
        )
        if not self.config.supersede_enabled or self.decision_fn is None or not existing_entry:
            return (
                "DROP",
                self._dropped(
                    cand, score, recall_count, diversity,
                    f"dropped: too similar (cosine={diversity:.3f}) to an existing entry",
                ),
            )

        try:
            decision = (await self.decision_fn(cand.raw_text, existing_entry)).strip().upper()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dreaming: decision_fn raised %s: %s; defaulting to DROP",
                type(exc).__name__,
                exc,
            )
            decision = "NOOP"

        if decision == "UPDATE" and self.replace_fn is not None:
            try:
                ok = bool(self.replace_fn(existing_entry, cand.raw_text))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dreaming: replace_fn raised %s: %s; dropping",
                    type(exc).__name__,
                    exc,
                )
                ok = False
            if ok:
                return (
                    "UPDATE",
                    DreamGateResult(
                        candidate=cand, outcome=DreamOutcome.UPDATED, score=score,
                        recall_count=recall_count, diversity_score=diversity,
                        rationale=f"superseded a stale entry (cosine={diversity:.3f})",
                        old_text=existing_entry,
                    ),
                )
            return ("DROP", self._dropped(cand, score, recall_count, diversity,
                                          "supersede replace failed; dropped"))

        if decision == "ADD":
            return ("ADD", self._dropped(cand, score, recall_count, diversity, "adjudged distinct"))

        return (
            "DROP",
            self._dropped(cand, score, recall_count, diversity,
                          f"dropped: adjudged duplicate (cosine={diversity:.3f})"),
        )
