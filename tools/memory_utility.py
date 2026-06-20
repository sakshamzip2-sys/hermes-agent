"""Read-only memory-utility rollup (Part 2, Slice 5 / extension memory-utility).

The unifying "utility = used x helpful" view over the memory planes. It joins two
substrates that ALREADY exist and already track usage + quality:

  - the skill sidecar (``tools/skill_usage.skill_health_view``): per-skill
    ``use_count`` and the additive ``success_rate`` / ``user_rating`` quality
    metrics folded by ``record_skill_outcome`` (Slice 3 + the feedback path);
  - the holographic fact store (``plugins/memory/holographic`` ``list_facts``):
    per-fact ``retrieval_count`` (used) and ``trust_score`` / ``helpful_count``
    (helpful) maintained by the fact store's own feedback path.

It is a PURE READ-ONLY ROLLUP. It creates no store, opens no write connection,
and mutates nothing. It returns rankable rows plus a sort helper. The decay
philosophy ("used + helpful is promoted, unused decays") is EXPRESSED here as a
``utility`` / ``decay`` SCORE only — it is NOT an eviction or auto-delete path
(retention/eviction is a separate, later concern). Nothing here ever drives a
prune, archive, or delete.

Design notes:
  - Every read is wrapped fail-soft: a missing/broken plane contributes an empty
    list, never an exception, so the view degrades gracefully (mirrors the
    sidecar's best-effort I/O contract).
  - No model, no network, no new HERMES_* env var. Pure arithmetic over data the
    two planes already persist.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Valid sort orders for :func:`sort_utility`. Presentation orderings only — they
# never feed a transition, prune, or eviction decision.
_UTILITY_SORTS = ("most_useful", "least_useful", "decaying")


def _as_float(value: Any) -> Optional[float]:
    """Best-effort float coercion; ``None`` for missing/non-numeric values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp01(value: float) -> float:
    """Clamp to [0, 1] so a score component never escapes the unit interval."""
    return max(0.0, min(1.0, value))


def _used_score(used_count: int) -> float:
    """Map a raw use/retrieval count to a saturating "used" signal in [0, 1].

    A diminishing-returns curve ``n / (n + 1)``: 0 uses -> 0.0, 1 -> 0.5, 3 ->
    0.75, growing toward 1.0. This keeps a heavily-used item from dwarfing a
    moderately-used one purely on raw count while still ordering by use.
    """
    n = max(0, int(used_count))
    return n / (n + 1.0)


def _utility(used_count: int, helpful: Optional[float]) -> float:
    """Combine "used" and "helpful" into a single utility score in [0, 1].

    ``utility = used_score(used_count) * helpful`` — the literal "used x helpful"
    the directive asks for. ``helpful`` is a quality signal already normalized to
    [0, 1] (skill ``success_rate``/``user_rating``; fact ``trust_score``). When
    no helpfulness signal exists yet (``None``), helpfulness is treated as the
    neutral prior 0.5 so a used-but-unrated item still ranks above a never-used
    one, without claiming it is "good".
    """
    h = 0.5 if helpful is None else _clamp01(helpful)
    return _clamp01(_used_score(used_count) * h)


def _decay(used_count: int, helpful: Optional[float]) -> float:
    """Decay score = 1 - utility. The "unused decays" half of the philosophy.

    Higher means "more decayed" (less used and/or less helpful). This is a SCORE
    for ranking/visibility only; it is NEVER an eviction trigger.
    """
    return _clamp01(1.0 - _utility(used_count, helpful))


def _skill_rows() -> List[Dict[str, Any]]:
    """Skill-plane utility rows from the sidecar health view (fail-soft)."""
    try:
        from tools.skill_usage import skill_health_view
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("utility_view: skill_usage import failed (%s)", exc)
        return []
    try:
        health = skill_health_view()
    except Exception as exc:  # noqa: BLE001 - best-effort read
        logger.debug("utility_view: skill_health_view failed (%s)", exc)
        return []

    rows: List[Dict[str, Any]] = []
    for r in health:
        used = int(r.get("use_count") or 0)
        # Prefer an explicit user_rating; fall back to outcome success_rate; both
        # are already [0, 1]. None when neither has a sample yet.
        helpful = _as_float(r.get("user_rating"))
        if helpful is None:
            helpful = _as_float(r.get("success_rate"))
        rows.append(
            {
                "plane": "skill",
                "key": r.get("name"),
                "used_count": used,
                "helpful": helpful,
                "utility": _utility(used, helpful),
                "decay": _decay(used, helpful),
                # Carry-through detail for display / debugging only.
                "success_rate": _as_float(r.get("success_rate")),
                "user_rating": _as_float(r.get("user_rating")),
                "sample_count": int(r.get("sample_count") or 0),
            }
        )
    return rows


def _fact_rows(fact_limit: int) -> List[Dict[str, Any]]:
    """Holographic-fact utility rows from the fact store (fail-soft, read-only).

    Uses ``MemoryStore.list_facts`` (a pure SELECT ordered by trust) and reads
    each fact's ``retrieval_count`` (used) + ``trust_score`` (helpful). When the
    store or numpy substrate is unavailable, returns ``[]`` rather than raising.
    """
    try:
        from plugins.memory.holographic.store import MemoryStore
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("utility_view: holographic import failed (%s)", exc)
        return []

    store: Optional[Any] = None
    try:
        store = MemoryStore()
        facts = store.list_facts(min_trust=0.0, limit=int(fact_limit))
    except Exception as exc:  # noqa: BLE001 - best-effort read
        logger.debug("utility_view: holographic list_facts failed (%s)", exc)
        return []
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:  # noqa: BLE001
                pass

    rows: List[Dict[str, Any]] = []
    for f in facts:
        used = int(f.get("retrieval_count") or 0)
        helpful = _as_float(f.get("trust_score"))
        rows.append(
            {
                "plane": "fact",
                "key": f.get("content"),
                "used_count": used,
                "helpful": helpful,
                "utility": _utility(used, helpful),
                "decay": _decay(used, helpful),
                # Carry-through detail for display / debugging only.
                "fact_id": f.get("fact_id"),
                "trust_score": helpful,
                "helpful_count": int(f.get("helpful_count") or 0),
            }
        )
    return rows


def utility_view(
    *, include_skills: bool = True, include_facts: bool = True, fact_limit: int = 200
) -> List[Dict[str, Any]]:
    """Return the unified "utility = used x helpful" rollup (pure read-only).

    Each row carries a stable shape regardless of plane:
      - ``plane``       — ``'skill'`` or ``'fact'``;
      - ``key``         — the skill name or the fact content;
      - ``used_count``  — skill ``use_count`` or fact ``retrieval_count``;
      - ``helpful``     — the [0, 1] quality signal (skill ``user_rating`` or
        ``success_rate``; fact ``trust_score``), or ``None`` when unrated;
      - ``utility``     — ``used x helpful`` in [0, 1] (the promotion signal);
      - ``decay``       — ``1 - utility`` (the "unused decays" signal),
    plus a few plane-specific carry-through fields for display.

    Reads each plane fail-soft (a missing/broken plane contributes nothing). This
    NEVER mutates any store and NEVER evicts: ``utility``/``decay`` are scores for
    ranking and visibility only. Use :func:`sort_utility` to rank the rows.
    """
    rows: List[Dict[str, Any]] = []
    if include_skills:
        rows.extend(_skill_rows())
    if include_facts:
        rows.extend(_fact_rows(fact_limit))
    return rows


def sort_utility(
    rows: List[Dict[str, Any]], order: str = "most_useful"
) -> List[Dict[str, Any]]:
    """Return *rows* (from :func:`utility_view`) sorted by *order*.

    Supported orders (:data:`_UTILITY_SORTS`):
      - ``most_useful``   — by ``utility`` descending (used + helpful first);
      - ``least_useful``  — by ``utility`` ascending (the bottom of the ranking);
      - ``decaying``      — by ``decay`` descending (most decayed first: the
        unused/unhelpful items the philosophy says fade). Equivalent ordering to
        ``least_useful`` since ``decay = 1 - utility``, but named for the decay
        framing and surfaced as a distinct intent.

    Does not mutate the input. An unknown *order* falls back to ``most_useful``.
    Ties break by ``plane`` then ``key`` for a stable, deterministic ordering.
    This is presentation only; it never triggers a prune or eviction.
    """
    if order not in _UTILITY_SORTS:
        order = "most_useful"

    def _tiebreak(r: Dict[str, Any]) -> Tuple[str, str]:
        return (str(r.get("plane") or ""), str(r.get("key") or ""))

    if order == "most_useful":
        return sorted(
            rows,
            key=lambda r: (-_clamp01(_as_float(r.get("utility")) or 0.0), *_tiebreak(r)),
        )
    if order == "decaying":
        return sorted(
            rows,
            key=lambda r: (-_clamp01(_as_float(r.get("decay")) or 0.0), *_tiebreak(r)),
        )
    # least_useful
    return sorted(
        rows,
        key=lambda r: (_clamp01(_as_float(r.get("utility")) or 0.0), *_tiebreak(r)),
    )
