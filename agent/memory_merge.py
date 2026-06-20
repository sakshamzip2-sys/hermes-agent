"""MergeLayer: combine-on-read retrieval-and-merge over the local memory planes.

This is the dark working slice of Decision A (PHASE3-design-decisions.md): a real
combined recall over the two LOCAL planes (session FTS5 + holographic facts),
ADDITIVE and self-contained. Nothing here is wired into the live recall path; the
whole feature ships behind ``merge.enabled: false`` until the req-#7 eval clears the
floor. It is importable and exercisable WITHOUT a live gateway: every adapter takes
an injected store/db handle, so unit tests drive it against temp stores.

The pipeline (PHASE3 section A "the hardened winner"):

    parallel fan-out over the provided local adapters
      -> per-plane sanitize_context + scan_for_threats(strict); DROP only the
         offending plane's hits (record planes_blocked), never whole-block blank
      -> semantic dedup (normalized-text-hash; HRR cosine collapse when vectors present)
      -> weighted RRF (k=60, per-plane weight default 1.0)
      -> source-tier multiplicative prior (user_authored 1.0 / curated 0.85 /
         bulk 0.5 / stale 0.5x) applied as an outer rerank
      -> per-source floors (a sole-source plane is never fully buried)
      -> abstention floor (return EMPTY if the top fused score is below threshold)
      -> return (ranked[:8], RecallTrace)

Defaults match the PHASE3 config block exactly:

    rrf_k: 60
    plane_weights: { local: 1.0, holographic: 1.0, honcho: 1.0, gbrain: 1.0 }
    source_tier_prior: { user_authored: 1.0, curated: 0.85, bulk: 0.5, stale_multiplier: 0.5 }

No em dashes (house rule). Pyright-clean.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# Sanitizer + scanner (the per-plane fence). Imported defensively so this module
# stays importable in trimmed environments / unit tests; the fallbacks are inert
# no-ops, never silently-permissive in the live tree because the real functions
# always import there.
# ---------------------------------------------------------------------------
try:
    from agent.memory_manager import sanitize_context as _sanitize_context
except Exception:  # pragma: no cover - defensive import only
    def _sanitize_context(text: str) -> str:
        return text

try:
    from tools.threat_patterns import scan_for_threats as _scan_for_threats
except Exception:  # pragma: no cover - defensive import only
    def _scan_for_threats(content: str, scope: str = "context") -> List[str]:
        return []

# HRR cosine for the optional semantic-dedup collapse. Numpy may be absent; the
# dedup then falls back to the normalized-text-hash only (still correct, just
# less aggressive about paraphrase collapse).
try:
    from plugins.memory.holographic import holographic as _hrr  # type: ignore
    _HRR_AVAILABLE = bool(getattr(_hrr, "_HAS_NUMPY", False))
except Exception:  # pragma: no cover - defensive import only
    _hrr = None  # type: ignore[assignment]
    _HRR_AVAILABLE = False


# ===========================================================================
# Defaults (frozen to the PHASE3 config block)
# ===========================================================================

RRF_K_DEFAULT = 60
PLANE_WEIGHTS_DEFAULT: Dict[str, float] = {
    "local": 1.0,
    "holographic": 1.0,
    "honcho": 1.0,
    "gbrain": 1.0,
}
SOURCE_TIER_PRIOR_DEFAULT: Dict[str, float] = {
    "user_authored": 1.0,
    "curated": 0.85,
    "bulk": 0.5,
}
STALE_MULTIPLIER_DEFAULT = 0.5

# Abstention: if the top fused (post-prior) score is below this, return EMPTY.
# Ships dark until gold-set-calibrated (PHASE3 mitigation #6 / config note). The
# default is deliberately low so the LOCAL working slice does not over-abstain
# before calibration; the eval's abstention cases prove empty-when-irrelevant.
ABSTENTION_FLOOR_DEFAULT = 0.0

# Final slot budget (PHASE3: "render top-8").
FINAL_SLOTS_DEFAULT = 8

# Per-store NL filler stripped before FTS5 MATCH (kept in sync with
# store._OR_STOPWORDS and skills/memory-eval/eval.py so the merge layer's
# expansion matches the adapters'). FTS5 implicitly ANDs terms, so NL filler
# forces misses; OR-joining the survivors recovers the hit (0.62 -> 1.00).
_OR_STOPWORDS = frozenset({
    "what", "is", "my", "do", "i", "the", "a", "an", "in", "of", "to",
    "where", "which", "are", "you", "does", "how", "me", "on", "for",
    "name", "when", "use", "uses", "used", "now", "current", "currently",
    "did", "was", "were", "have", "has", "and", "or", "with", "that",
})
_RE_NONWORD = re.compile(r"\W+", re.UNICODE)
_RE_WS = re.compile(r"\s+")


def expand_query_or(query: str) -> str:
    """Expand a natural-language query into an FTS5 ``term OR term ...`` query.

    Splits on non-word characters, lowercases, drops a small stopword set and
    single-character tokens, OR-joins the survivors. Returns the stripped
    original when nothing survives (so a degenerate all-stopword query still
    searches for something). Pure function, no I/O. This is the same
    deterministic pass the holographic adapter and session adapter both apply
    so the trace's ``expanded_query`` is exactly what hit the stores.
    """
    terms = [
        t for t in _RE_NONWORD.split(query.lower())
        if len(t) > 1 and t not in _OR_STOPWORDS
    ]
    return " OR ".join(terms) if terms else query.strip()


def _normalize_text(text: str) -> str:
    """Lowercase + collapse whitespace + strip, for the dedup text-hash key."""
    return _RE_WS.sub(" ", (text or "").strip().lower())


# ===========================================================================
# Candidate envelope
# ===========================================================================

@dataclass
class Candidate:
    """A single retrieval hit, normalized across stores for fusion.

    Attributes
    ----------
    id:
        Stable per-store identifier (e.g. holographic ``fact_id`` or the session
        message id). Stringified so cross-store ids never collide on type.
    text_for_rerank:
        The candidate's text, used for the per-plane threat scan, the dedup
        hash, and (optionally) the HRR-cosine collapse. This is the content a
        downstream reranker would see.
    source_store:
        Logical plane name (``"session"``, ``"holographic"``, ``"honcho"``,
        ``"gbrain"``). Also the key into ``plane_weights``.
    native_rank:
        1-indexed rank within the source store's own result list (1 = best).
    native_score:
        The store's own score when it exposes one (FTS5 bm25, trust, etc.).
        Optional: RRF uses rank, not score, so this is for the trace only.
    metadata:
        Free-form per-store extras (category, role, session_id, source_tier,
        stale flag, hrr_vector bytes for cosine dedup). Never used for ranking
        except the keys this module explicitly reads (``source_tier``,
        ``stale``, ``hrr_vector``).
    """

    id: str
    text_for_rerank: str
    source_store: str
    native_rank: int
    native_score: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# Adapter protocol + the two LOCAL adapters
# ===========================================================================

class StoreAdapter(Protocol):
    """A per-store adapter: query a single plane, return ranked Candidates.

    Implementations take their store/db handle by injection (constructor), so
    they are unit-testable against a temp store with no live gateway.
    """

    name: str

    def search(self, query: str, *, limit: int) -> List[Candidate]:
        ...


class SessionFTS5Adapter:
    """Adapter over the session FTS5 plane (hermes_state.search_messages).

    Maps message rows (dict with id/session_id/role/snippet/content) to
    Candidates. The injected ``db`` is anything exposing ``search_messages`` with
    the hermes_state signature, so a temp ``SessionDB`` drives it in tests.

    The query is OR-expanded before the MATCH (FTS5 implicit-AND fix). The
    ``scope`` (lineage filter) is left to the caller / Decision-B part-1 core
    touch; this adapter passes through ``source_filter`` / ``role_filter`` /
    ``exclude_sources`` so the working slice can scope its own eval seed without
    waiting on the core change.
    """

    def __init__(
        self,
        db: Any,
        *,
        name: str = "session",
        source_tier: str = "curated",
        role_filter: Optional[List[str]] = None,
        source_filter: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> None:
        self._db = db
        self.name = name
        self._source_tier = source_tier
        self._role_filter = role_filter
        self._source_filter = source_filter
        self._exclude_sources = exclude_sources

    def search(self, query: str, *, limit: int) -> List[Candidate]:
        expanded = expand_query_or(query)
        if not expanded:
            return []
        rows = self._db.search_messages(
            expanded,
            source_filter=self._source_filter,
            exclude_sources=self._exclude_sources,
            role_filter=self._role_filter,
            limit=limit,
        )
        candidates: List[Candidate] = []
        for rank, row in enumerate(rows or [], start=1):
            # search_messages returns dict rows; prefer full content, fall back
            # to the snippet (strip FTS5 highlight markers) when content absent.
            text = row.get("content")
            if not isinstance(text, str) or not text.strip():
                snippet = row.get("snippet") or ""
                text = snippet.replace(">>>", "").replace("<<<", "")
            meta: Dict[str, Any] = {
                "session_id": row.get("session_id"),
                "role": row.get("role"),
                "source": row.get("source"),
                "source_tier": self._source_tier,
            }
            candidates.append(Candidate(
                id=str(row.get("id")),
                text_for_rerank=text or "",
                source_store=self.name,
                native_rank=rank,
                native_score=None,
                metadata=meta,
            ))
        return candidates


class HolographicAdapter:
    """Adapter over the holographic FTS5 fact plane (store.search_facts_readonly).

    Maps fact rows to Candidates. Uses the READ-ONLY variant (no write on read,
    ro WAL connection) with internal OR-expansion. The injected ``store`` is a
    ``MemoryStore`` (or anything exposing ``search_facts_readonly``), so a temp
    store drives it in tests with no live gateway.

    ``min_trust=0.0`` by default so default-trust facts (0.5) are not floored
    out before fusion sees them; trust still flows into the trace via
    ``native_score`` and into the source-tier prior via ``metadata``.
    """

    def __init__(
        self,
        store: Any,
        *,
        name: str = "holographic",
        source_tier: str = "user_authored",
        min_trust: float = 0.0,
        category: Optional[str] = None,
    ) -> None:
        self._store = store
        self.name = name
        self._source_tier = source_tier
        self._min_trust = min_trust
        self._category = category

    def search(self, query: str, *, limit: int) -> List[Candidate]:
        # store.search_facts_readonly does the OR-expansion itself when asked;
        # we pass or_expand=True so the NL->OR fix runs at the store boundary.
        rows = self._store.search_facts_readonly(
            query,
            category=self._category,
            min_trust=self._min_trust,
            limit=limit,
            or_expand=True,
        )
        candidates: List[Candidate] = []
        for rank, row in enumerate(rows or [], start=1):
            trust = row.get("trust_score")
            meta: Dict[str, Any] = {
                "category": row.get("category"),
                "tags": row.get("tags"),
                "trust_score": trust,
                "source_tier": self._source_tier,
            }
            if "hrr_vector" in row and row.get("hrr_vector") is not None:
                meta["hrr_vector"] = row["hrr_vector"]
            candidates.append(Candidate(
                id=str(row.get("fact_id")),
                text_for_rerank=str(row.get("content") or ""),
                source_store=self.name,
                native_rank=rank,
                native_score=float(trust) if isinstance(trust, (int, float)) else None,
                metadata=meta,
            ))
        return candidates


# ===========================================================================
# Pure RRF (unit-testable in isolation)
# ===========================================================================

def reciprocal_rank_fusion(
    ranked_lists: Sequence[Tuple[str, Sequence[str]]],
    *,
    k: int = RRF_K_DEFAULT,
    weights: Optional[Dict[str, float]] = None,
) -> List[Tuple[str, float]]:
    """Weighted Reciprocal Rank Fusion.

    Parameters
    ----------
    ranked_lists:
        A sequence of ``(plane_name, [item_id, item_id, ...])`` pairs, each list
        already ordered best-first. ``item_id`` is any hashable key; the SAME
        key appearing in two planes is fused (cross-store consensus).
    k:
        The RRF constant (default 60, per PHASE3). A larger k flattens the
        contribution of top ranks; 60 is the canonical value.
    weights:
        Optional per-plane multiplier on that plane's RRF contribution. Missing
        planes default to 1.0.

    Returns
    -------
    A list of ``(item_id, fused_score)`` sorted by fused_score descending. The
    score for an item is ``sum over planes of weight[plane] / (k + rank)``,
    rank being 1-indexed within that plane's list. An item absent from a plane
    contributes 0 from it (graceful degradation: a missing/empty store just
    does not add to any score).

    Pure function: no I/O, deterministic, ties broken by item_id for stability.
    """
    w = weights or {}
    scores: Dict[str, float] = {}
    for plane, items in ranked_lists:
        plane_weight = float(w.get(plane, 1.0))
        for rank, item_id in enumerate(items, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + plane_weight / (k + rank)
    # Sort by score desc, then id asc for a stable, deterministic order.
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


# ===========================================================================
# RecallTrace
# ===========================================================================

def _new_trace(query: str, expanded: str) -> Dict[str, Any]:
    """Build an empty RecallTrace dict with every documented key present."""
    return {
        "query": query,
        "expanded_query": expanded,
        "planes_queried": [],
        "planes_blocked": [],
        "planes_timed_out": [],
        "per_plane_hits": [],          # [{store, id, native_rank, native_score}]
        "fused_order": [],             # [item_key, ...] post-RRF, pre-prior order
        "source_tier_multipliers": {}, # {item_key: multiplier}
        "final_slots": [],             # [{store, id, fused_score, final_score}]
        "per_plane_latency_ms": {},    # {store: float}
        "total_latency_ms": 0.0,
        "abstained": False,
    }


# ===========================================================================
# MergeLayer
# ===========================================================================

class MergeLayer:
    """Parallel-fan-out + RRF + source-tier prior + per-source floors + abstention.

    The working slice runs SYNCHRONOUSLY over the provided LOCAL adapters (no
    remote planes, no event loop). Remote planes + their deadlines arrive in a
    later step; the design keeps this class additive and behind
    ``merge.enabled: false``.
    """

    def __init__(
        self,
        *,
        rrf_k: int = RRF_K_DEFAULT,
        plane_weights: Optional[Dict[str, float]] = None,
        source_tier_prior: Optional[Dict[str, float]] = None,
        stale_multiplier: float = STALE_MULTIPLIER_DEFAULT,
        abstention_floor: float = ABSTENTION_FLOOR_DEFAULT,
        per_source_floors: bool = True,
        final_slots: int = FINAL_SLOTS_DEFAULT,
        per_store_limit: int = 20,
        enable_hrr_dedup: bool = True,
        scan_scope: str = "strict",
    ) -> None:
        self.rrf_k = rrf_k
        self.plane_weights = dict(PLANE_WEIGHTS_DEFAULT)
        if plane_weights:
            self.plane_weights.update(plane_weights)
        self.source_tier_prior = dict(SOURCE_TIER_PRIOR_DEFAULT)
        if source_tier_prior:
            self.source_tier_prior.update(source_tier_prior)
        self.stale_multiplier = stale_multiplier
        self.abstention_floor = abstention_floor
        self.per_source_floors = per_source_floors
        self.final_slots = final_slots
        self.per_store_limit = per_store_limit
        self.enable_hrr_dedup = enable_hrr_dedup and _HRR_AVAILABLE
        self.scan_scope = scan_scope

    # -- internal key: store-qualified so the same id in two stores stays distinct
    @staticmethod
    def _key(cand: Candidate) -> str:
        return f"{cand.source_store}#{cand.id}"

    # -- per-plane fence: sanitize then scan; True => the plane is poisoned
    def _plane_is_poisoned(self, candidates: Sequence[Candidate]) -> bool:
        for c in candidates:
            cleaned = _sanitize_context(c.text_for_rerank or "")
            if _scan_for_threats(cleaned, scope=self.scan_scope):
                return True
        return False

    # -- source-tier multiplicative prior for one candidate
    def _tier_multiplier(self, cand: Candidate) -> float:
        tier = str(cand.metadata.get("source_tier", "")) if cand.metadata else ""
        mult = self.source_tier_prior.get(tier, 1.0)
        if cand.metadata and cand.metadata.get("stale"):
            mult *= self.stale_multiplier
        return mult

    # -- semantic dedup: collapse paraphrases / cross-store re-statements
    def _dedup(
        self, candidates: List[Candidate]
    ) -> Tuple[List[Candidate], Dict[str, str]]:
        """Collapse near-duplicate candidates to one representative.

        Strategy:
          1. exact normalized-text-hash collapse (cheap, always on);
          2. HRR-cosine collapse of near-dups when vectors are present and numpy
             is available (paraphrase / cross-store consensus).

        Returns the surviving candidates (first-seen wins, preserving the order
        they were passed in) plus a ``{dropped_key: kept_key}`` map for the
        trace. RRF later treats a kept representative as appearing in EACH plane
        whose member collapsed into it, so cross-store consensus still rewards
        the survivor without double-counting paraphrases as independent hits.
        """
        survivors: List[Candidate] = []
        by_hash: Dict[str, Candidate] = {}
        merged: Dict[str, str] = {}
        # HRR vectors for survivors that carry one, for the cosine pass.
        survivor_vecs: List[Tuple[Candidate, Any]] = []

        for cand in candidates:
            norm = _normalize_text(cand.text_for_rerank)
            if norm and norm in by_hash:
                merged[self._key(cand)] = self._key(by_hash[norm])
                continue

            # HRR-cosine near-dup check against existing survivors.
            collapsed_into: Optional[Candidate] = None
            if self.enable_hrr_dedup and survivor_vecs:
                vec = self._candidate_vector(cand)
                if vec is not None:
                    for other, ovec in survivor_vecs:
                        if ovec is None:
                            continue
                        try:
                            sim = _hrr.similarity(vec, ovec)  # type: ignore[union-attr]
                        except Exception:
                            continue
                        if sim >= 0.92:
                            collapsed_into = other
                            break
            if collapsed_into is not None:
                merged[self._key(cand)] = self._key(collapsed_into)
                continue

            survivors.append(cand)
            if norm:
                by_hash[norm] = cand
            if self.enable_hrr_dedup:
                survivor_vecs.append((cand, self._candidate_vector(cand)))

        return survivors, merged

    def _candidate_vector(self, cand: Candidate) -> Any:
        """Best-effort HRR phase vector for a candidate, or None.

        Prefers a stored ``hrr_vector`` (bytes) in metadata; falls back to
        encoding the text on the fly. Any failure returns None so dedup quietly
        degrades to the text-hash path.
        """
        if not self.enable_hrr_dedup or _hrr is None:
            return None
        try:
            blob = cand.metadata.get("hrr_vector") if cand.metadata else None
            if isinstance(blob, (bytes, bytearray)):
                return _hrr.bytes_to_phases(bytes(blob))
            text = cand.text_for_rerank or ""
            if not text.strip():
                return None
            return _hrr.encode_text(text)
        except Exception:
            return None

    def recall(
        self,
        query: str,
        *,
        stores: Sequence[StoreAdapter],
        now: Optional[Callable[[], float]] = None,
    ) -> Tuple[List[Candidate], Dict[str, Any]]:
        """Combined recall over the provided LOCAL adapters.

        Steps (PHASE3 "the hardened winner"):
          1. NL->OR expand the query (for the trace; each adapter also expands);
          2. fan out to each adapter, timing it;
          3. per plane: sanitize + scan(strict); DROP the offending plane's hits
             entirely and record it in ``planes_blocked`` (not whole-block blank);
          4. semantic dedup (text-hash + optional HRR cosine);
          5. weighted RRF (k, per-plane weight);
          6. source-tier multiplicative prior as an outer rerank;
          7. per-source floors so a sole-source plane is never fully buried;
          8. abstention floor: EMPTY if the top final score < threshold;
          9. return (ranked[:final_slots], RecallTrace).

        Returns ``(ranked_candidates, trace)``. ``trace`` always has every
        documented key (see ``_new_trace``).
        """
        clock = now or time.perf_counter
        t_start = clock()
        expanded = expand_query_or(query)
        trace = _new_trace(query, expanded)

        # ---- 2 + 3: fan out, time, per-plane fence ----------------------
        kept_by_plane: List[Tuple[str, List[Candidate]]] = []
        cand_by_key: Dict[str, Candidate] = {}

        for adapter in stores:
            plane = adapter.name
            trace["planes_queried"].append(plane)
            t0 = clock()
            try:
                hits = adapter.search(query, limit=self.per_store_limit)
            except Exception:
                # A failing adapter contributes nothing; RRF degrades. Record
                # it as timed-out/down so the supervisor surface is non-empty.
                trace["planes_timed_out"].append(plane)
                trace["per_plane_latency_ms"][plane] = round(
                    (clock() - t0) * 1000.0, 4
                )
                continue
            trace["per_plane_latency_ms"][plane] = round((clock() - t0) * 1000.0, 4)

            # Record native attribution for EVERY hit before any drop, so the
            # trace shows what the plane returned even when it is blocked.
            for c in hits:
                trace["per_plane_hits"].append({
                    "store": plane,
                    "id": c.id,
                    "native_rank": c.native_rank,
                    "native_score": c.native_score,
                })

            # Per-plane sanitize + scan; drop the WHOLE plane on any hit.
            if self._plane_is_poisoned(hits):
                trace["planes_blocked"].append(plane)
                continue

            kept_by_plane.append((plane, hits))
            for c in hits:
                cand_by_key.setdefault(self._key(c), c)

        # ---- 4: semantic dedup across surviving planes ------------------
        all_kept: List[Candidate] = [c for _, hits in kept_by_plane for c in hits]
        survivors, merged_map = self._dedup(all_kept)
        survivor_keys = {self._key(c) for c in survivors}

        # Rewrite each plane's id list to its surviving representative, in
        # native order, deduped within the plane. A candidate merged into a
        # representative from ANOTHER plane still contributes its plane's vote
        # to that representative (cross-store consensus without double-count).
        def _rep_key(c: Candidate) -> str:
            k = self._key(c)
            return merged_map.get(k, k)

        ranked_lists: List[Tuple[str, List[str]]] = []
        for plane, hits in kept_by_plane:
            seen: set[str] = set()
            ordered: List[str] = []
            for c in hits:
                rk = _rep_key(c)
                if rk in survivor_keys and rk not in seen:
                    seen.add(rk)
                    ordered.append(rk)
            ranked_lists.append((plane, ordered))

        # ---- 5: weighted RRF -------------------------------------------
        fused = reciprocal_rank_fusion(
            ranked_lists, k=self.rrf_k, weights=self.plane_weights
        )
        trace["fused_order"] = [key for key, _ in fused]

        # ---- 6: source-tier multiplicative prior (outer rerank) ---------
        scored: List[Tuple[str, float, float]] = []  # (key, fused, final)
        for key, fused_score in fused:
            cand = cand_by_key.get(key)
            mult = self._tier_multiplier(cand) if cand is not None else 1.0
            trace["source_tier_multipliers"][key] = mult
            scored.append((key, fused_score, fused_score * mult))
        scored.sort(key=lambda t: (-t[2], t[0]))

        # ---- 7: per-source floors --------------------------------------
        # Guarantee at least one survivor from EACH plane that returned hits is
        # present in the final slot budget, so a sole-source plane is never
        # fully buried under another plane's volume.
        order_keys = [k for k, _, _ in scored]
        final_score_by_key = {k: fs for k, _, fs in scored}
        fused_score_by_key = {k: f for k, f, _ in scored}

        if self.per_source_floors and order_keys:
            # order_keys is sorted by final score (desc), so the first key seen
            # for each plane is that plane's best-scoring survivor.
            best_key_per_plane: dict = {}
            for k in order_keys:
                p = k.split("#", 1)[0]
                if p not in best_key_per_plane:
                    best_key_per_plane[p] = k
            default_budget = order_keys[: self.final_slots]
            present_planes = {k.split("#", 1)[0] for k in default_budget}
            # Pin one survivor for every plane that returned hits but is absent
            # from the default budget. Pinned keys are GUARANTEED a final slot and
            # are NEVER evicted by the final re-sort. This is the actual floor: a
            # low-tier sole-source plane must not be clipped out by a high-volume
            # higher-tier plane (the previous re-sort silently undid the rescue).
            pinned: List[str] = []
            for plane, hits in kept_by_plane:
                if not hits or plane in present_planes:
                    continue
                rescued = best_key_per_plane.get(plane)
                if rescued is not None and rescued not in pinned:
                    pinned.append(rescued)
            if pinned:
                pinned = pinned[: self.final_slots]
                pinned_set = set(pinned)
                fill = [k for k in order_keys if k not in pinned_set]
                keep = pinned + fill[: max(0, self.final_slots - len(pinned))]
                # Present in final-score order, but membership is guaranteed:
                # every pinned key survives even if it is the lowest scorer.
                order_keys = sorted(
                    keep, key=lambda k: (-final_score_by_key.get(k, 0.0), k)
                )
            else:
                order_keys = default_budget

        # ---- 8: abstention floor ---------------------------------------
        top_final = final_score_by_key.get(order_keys[0], 0.0) if order_keys else 0.0
        if not order_keys or top_final < self.abstention_floor:
            trace["abstained"] = True
            trace["final_slots"] = []
            trace["total_latency_ms"] = round((clock() - t_start) * 1000.0, 4)
            return [], trace

        # ---- 9: materialize the ranked candidates ----------------------
        ranked: List[Candidate] = []
        for key in order_keys[: self.final_slots]:
            cand = cand_by_key.get(key)
            if cand is None:
                continue
            ranked.append(cand)
            trace["final_slots"].append({
                "store": cand.source_store,
                "id": cand.id,
                "fused_score": round(fused_score_by_key.get(key, 0.0), 8),
                "final_score": round(final_score_by_key.get(key, 0.0), 8),
            })

        trace["total_latency_ms"] = round((clock() - t_start) * 1000.0, 4)
        return ranked, trace


__all__ = [
    "Candidate",
    "StoreAdapter",
    "SessionFTS5Adapter",
    "HolographicAdapter",
    "MergeLayer",
    "reciprocal_rank_fusion",
    "expand_query_or",
    "RRF_K_DEFAULT",
    "PLANE_WEIGHTS_DEFAULT",
    "SOURCE_TIER_PRIOR_DEFAULT",
    "STALE_MULTIPLIER_DEFAULT",
    "ABSTENTION_FLOOR_DEFAULT",
    "FINAL_SLOTS_DEFAULT",
]
