"""Clustering pre-gate — collapse near-duplicate candidate facts before scoring.

Ported from v1's ``dreaming_cluster.py``: greedy single-linkage over cosine similarity.
Each candidate is compared to existing cluster representatives; the first rep within
``similarity_threshold`` absorbs it. This prevents seven near-identical extracted facts
from producing seven near-identical promotions, and saves redundant scoring calls.

Replaces the runner's exact-text dedup with semantic (or lexical-fallback) near-dup
collapsing. When no usable embedder is available the cosines are 0 and every candidate
stays its own cluster (degrades to "no clustering", never crashes).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Optional

from .engine import DreamCandidate, _cosine

logger = logging.getLogger("hermes.plugins.dreaming.cluster")

EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]


async def cluster_candidates(
    candidates: list[DreamCandidate],
    *,
    embed_fn: Optional[EmbedFn],
    similarity_threshold: float = 0.7,
    min_cluster_size: int = 2,
) -> list[DreamCandidate]:
    """Collapse near-duplicate candidates to one representative each.

    The representative is the first (earliest) member of each cluster; its metadata
    records ``cluster_size`` and ``cluster_member_ids`` for audit. Clusters smaller than
    ``min_cluster_size`` pass through unchanged.
    """
    if embed_fn is None or len(candidates) < 2:
        return candidates
    try:
        vectors = await embed_fn([c.raw_text for c in candidates])
    except Exception as exc:  # noqa: BLE001 — never break the pipeline on clustering
        logger.warning("dreaming: clustering embed failed (%s); skipping clustering", exc)
        return candidates
    if not vectors or len(vectors) != len(candidates):
        return candidates

    # Greedy single-linkage: assign each candidate to the first rep it's close to.
    reps: list[int] = []           # indices of cluster representatives
    members: dict[int, list[int]] = {}
    for i in range(len(candidates)):
        placed = False
        for r in reps:
            if _cosine(vectors[i], vectors[r]) >= similarity_threshold:
                members[r].append(i)
                placed = True
                break
        if not placed:
            reps.append(i)
            members[i] = [i]

    out: list[DreamCandidate] = []
    for r in reps:
        idxs = members[r]
        rep = candidates[r]
        if len(idxs) < min_cluster_size:
            out.append(rep)
            continue
        merged_meta = dict(rep.metadata)
        merged_meta["cluster_size"] = len(idxs)
        merged_meta["cluster_member_ids"] = [candidates[j].event_id for j in idxs]
        out.append(
            DreamCandidate(
                event_id=rep.event_id,
                raw_text=rep.raw_text,
                timestamp_ns=rep.timestamp_ns,
                metadata=merged_meta,
            )
        )
    return out
