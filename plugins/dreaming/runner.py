"""Orchestration: turn recent sessions into MEMORY.md promotions.

``run_dream_cycle`` is the single entry point. It is safe to call from a session
hook (it never raises into the caller) and from the CLI. The opportunistic
trigger debounces on ``min_interval_hours`` so a dream cycle runs at most that
often regardless of how many sessions start.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from . import candidates as candmod
from . import llm, memory_io
from .config import DreamingPluginConfig, load_dreaming_config
from .engine import DreamCandidate, DreamingPipeline, DreamRunSummary
from .store import DreamStore

logger = logging.getLogger("hermes.plugins.dreaming.runner")

# How far back to look on the very first run (no last_run_ts yet), in seconds.
_FIRST_RUN_LOOKBACK = 30 * 24 * 60 * 60  # 30 days

# If the gap since the last run exceeds this many intervals, do one wider catch-up pass.
_CATCH_UP_FACTOR = 2.0

_run_lock = threading.Lock()


def _store_path() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "dreaming" / "dreaming.db"
    except Exception:  # noqa: BLE001
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "dreaming" / "dreaming.db"


def _review_home() -> Path:
    """Where the HMAC review queue lives (``$HERMES_HOME/dreaming``)."""
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "dreaming"
    except Exception:  # noqa: BLE001
        import os

        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "dreaming"


def _candidate_id(session_event_id: str, fact: str) -> str:
    return hashlib.sha256(f"{session_event_id}|{fact}".encode()).hexdigest()[:16]


def _queue_for_review(summary: DreamRunSummary) -> None:
    """Queue gate-passing promotions/updates to the HMAC review queue (review_mode).

    The engine already built full DreamGateResults (text + score + recall + diversity),
    so we queue each with its provenance instead of having written MEMORY.md. Fail-soft.
    """
    from . import review

    home = _review_home()
    for r in summary.promoted:
        try:
            review.queue_pending(
                home, text=r.candidate.raw_text, source_event_id=r.candidate.event_id,
                score=r.score, recall_count=r.recall_count, diversity_score=r.diversity_score,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dreaming: review queue failed for a promotion: %s", exc)
    for r in summary.updated:
        try:
            review.queue_pending(
                home, text=r.candidate.raw_text, source_event_id=r.candidate.event_id,
                score=r.score, recall_count=r.recall_count, diversity_score=r.diversity_score,
                old_text=r.old_text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("dreaming: review queue failed for an update: %s", exc)


async def run_dream_cycle(
    *,
    force: bool = False,
    config: Optional[DreamingPluginConfig] = None,
    db_path: Optional[Path] = None,
    store: Optional[DreamStore] = None,
) -> DreamRunSummary:
    """Run one consolidation pass. Returns the summary (empty on skip/error).

    Args:
        force: bypass the enabled flag and the debounce interval (manual run).
        config/db_path/store: injectable for tests; resolved from the live
            profile otherwise.
    """
    cfg = config or load_dreaming_config()
    if not cfg.enabled and not force:
        logger.debug("dreaming: disabled; skipping")
        return DreamRunSummary()

    # SENSE → DREAM: nudge the promotion bar by recent turn-outcomes (fail-soft; the
    # base threshold is returned unchanged when the outcomes plugin is absent/empty).
    import dataclasses as _dc

    from .outcome_link import adjusted_score_threshold

    tuned = adjusted_score_threshold(cfg.engine.score_threshold)
    if tuned != cfg.engine.score_threshold:
        cfg = _dc.replace(cfg, engine=_dc.replace(cfg.engine, score_threshold=tuned))

    sdb = db_path or candmod.default_state_db_path()
    if sdb is None:
        logger.debug("dreaming: no state.db resolvable; skipping")
        return DreamRunSummary()

    st = store or DreamStore(_store_path())

    now = time.time()
    last_run = st.last_run_ts()
    if not force and last_run and (now - last_run) < cfg.min_interval_seconds:
        logger.debug("dreaming: within debounce window; skipping")
        return DreamRunSummary()

    since = last_run if last_run else (now - _FIRST_RUN_LOOKBACK)

    # Marker-stripped MEMORY.md entries are the diversity-gate corpus, so the
    # "(dreamed DATE)" prefix tokens don't dilute the similarity comparison.
    existing_facts = memory_io.read_memory_facts()

    # --- Pass 1: re-score the DREAMS.md holding pen ------------------------
    # A fact held earlier (low recall / low score) gets another chance as recall
    # accumulates over time. Promoted ones leave DREAMS.md; still-weak ones stay.
    rescore = await _rescore_dreams(cfg, sdb, existing_facts)
    if rescore.promoted or rescore.updated:
        existing_facts = memory_io.read_memory_facts()  # refresh after promotions

    # --- Pass 2: new sessions since the last run ---------------------------
    # Cron-miss catch-up: if we missed several intervals (outage), widen the fetch
    # for this one recovery pass so a long gap doesn't silently drop facts.
    fetch_limit = cfg.candidate_fetch_limit
    interval_s = cfg.min_interval_seconds
    if last_run and interval_s > 0 and (now - last_run) > _CATCH_UP_FACTOR * interval_s:
        fetch_limit = cfg.candidate_fetch_limit * 4
        logger.info("dreaming: catch-up pass (gap %.1fh); fetch limit %d",
                    (now - last_run) / 3600.0, fetch_limit)
    digests = candmod.build_session_digests(sdb, since_ts=since, limit=fetch_limit)

    # A durable fact often recurs across sessions, so identical extracted facts
    # collapse to one candidate (the v1 clustering pre-gate analog) — preventing
    # duplicate promotions and redundant scoring calls. The candidate id keys on
    # the normalised fact text so the idempotency ledger also catches the same
    # fact in a later run.
    cand_facts: list[DreamCandidate] = []
    fact_by_id: dict[str, str] = {}
    seen_facts: set[str] = set()
    for dg in digests:
        try:
            facts = await llm.extract_facts(dg.text)
        except llm.RateLimitedError:
            logger.warning("dreaming: rate-limited during extraction; stopping early")
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning("dreaming: extraction failed for a session: %s", exc)
            continue
        for fact in facts:
            norm = " ".join(fact.lower().split())
            if not norm or norm in seen_facts:
                continue
            # No-recursion invariant: never re-dream a cross-fed/imported line.
            # A derived fact (provenance-tagged by the orchestrator's importer)
            # must be excluded from the candidate pool so it can't loop back
            # through extraction -> promotion -> re-extraction (model collapse).
            if candmod.is_derived_fact(fact):
                continue
            seen_facts.add(norm)
            cid = _candidate_id(norm, fact)
            fact_by_id[cid] = fact
            cand_facts.append(
                DreamCandidate(
                    event_id=cid,
                    raw_text=fact,
                    timestamp_ns=int(dg.last_ts * 1e9),
                    metadata={"session_id": dg.session_id},
                )
            )

    # Semantic clustering pre-gate: collapse near-duplicate extracted facts (beyond the
    # exact-text dedup above) so paraphrases of the same fact don't each get promoted.
    if len(cand_facts) > 1:
        from .cluster import cluster_candidates

        cand_facts = await cluster_candidates(
            cand_facts,
            embed_fn=llm.semantic_embed,
            similarity_threshold=cfg.cluster_similarity_threshold,
        )

    session_summary = DreamRunSummary()
    if cand_facts:
        promote_fn, replace_fn = _review_mode_fns(cfg)
        pipeline = _build_pipeline(cfg, sdb, fact_by_id, hold_fn=memory_io.hold,
                                   promote_fn=promote_fn, replace_fn=replace_fn)
        already = st.processed_ids()
        session_summary = await pipeline.run_once(
            cand_facts,
            existing_memories=existing_facts,
            already_processed_event_ids=already,
        )
        # Mark every evaluated session candidate processed so sessions aren't
        # re-extracted. Held facts now live in DREAMS.md and are re-promoted via
        # the Pass-1 re-score (above), not by re-scanning the session.
        evaluated_ids = [c.event_id for c in cand_facts if c.event_id not in already]
        st.mark_processed(evaluated_ids)

    # review_mode: gate-passing facts were NOT written to MEMORY.md (no-op promote/replace);
    # queue them to the HMAC review queue for operator accept/reject instead.
    if getattr(cfg, "review_mode", False):
        _queue_for_review(_merge_summaries(rescore, session_summary))

    st.set_last_run_ts(now)
    combined = _merge_summaries(rescore, session_summary)
    st.record_run(combined.counts())

    counts = combined.counts()
    if counts["promoted"] or counts["updated"] or counts["held"]:
        logger.info(
            "dreaming: promoted=%d updated=%d held=%d dropped=%d (evaluated=%d)",
            counts["promoted"], counts["updated"], counts["held"],
            counts["dropped"], counts["evaluated"],
        )
    return combined


def _build_recall_fn(sdb: Path, fact_by_id: dict[str, str]):
    def recall_count_fn(event_id: str) -> int:
        fact = fact_by_id.get(event_id, "")
        return candmod.count_sessions_matching(sdb, candmod.salient_terms(fact))

    return recall_count_fn


def _build_pipeline(
    cfg, sdb: Path, fact_by_id: dict[str, str], *, hold_fn,
    promote_fn=None, replace_fn=None,
) -> DreamingPipeline:
    return DreamingPipeline(
        cfg.engine,
        score_fn=llm.score_fact,
        recall_count_fn=_build_recall_fn(sdb, fact_by_id),
        promote_fn=promote_fn or memory_io.promote,
        hold_fn=hold_fn,
        embed_fn=llm.semantic_embed,  # semantic (config-driven), lexical fallback
        decision_fn=llm.decide_supersede,
        replace_fn=replace_fn or memory_io.replace,
    )


def _review_mode_fns(cfg):
    """In review_mode, promote/replace become no-ops (don't touch MEMORY.md); the engine
    still routes to PROMOTED/UPDATED so we can queue the results with full metadata."""
    if getattr(cfg, "review_mode", False):
        return (lambda _text: None, lambda _old, _new: True)
    return (None, None)


def _merge_summaries(a: DreamRunSummary, b: DreamRunSummary) -> DreamRunSummary:
    return DreamRunSummary(
        promoted=a.promoted + b.promoted,
        held=a.held + b.held,
        dropped=a.dropped + b.dropped,
        updated=a.updated + b.updated,
        skipped_already_processed=a.skipped_already_processed + b.skipped_already_processed,
        total_evaluated=a.total_evaluated + b.total_evaluated,
        rate_limited=a.rate_limited or b.rate_limited,
    )


async def _rescore_dreams(cfg, sdb: Path, existing_facts: list[str]) -> DreamRunSummary:
    """Re-evaluate DREAMS.md held facts; promote any that now pass; rewrite the pen."""
    dreams_facts = memory_io.read_dreams_facts()
    if not dreams_facts:
        return DreamRunSummary()

    cand: list[DreamCandidate] = []
    fact_by_id: dict[str, str] = {}
    seen: set[str] = set()
    for f in dreams_facts:
        norm = " ".join(f.lower().split())
        if not norm or norm in seen:
            continue
        if candmod.is_derived_fact(f):  # no-recursion invariant (see above)
            continue
        seen.add(norm)
        cid = _candidate_id(norm, f)
        fact_by_id[cid] = f
        cand.append(DreamCandidate(event_id=cid, raw_text=f))

    # hold_fn is a no-op here: these facts already live in DREAMS.md and we
    # rewrite the file from `remaining` afterwards (avoids duplicate appends).
    promote_fn, replace_fn = _review_mode_fns(cfg)
    pipeline = _build_pipeline(cfg, sdb, fact_by_id, hold_fn=lambda *_args: None,
                               promote_fn=promote_fn, replace_fn=replace_fn)
    summary = await pipeline.run_once(cand, existing_memories=existing_facts)

    gone = {r.candidate.raw_text for r in (*summary.promoted, *summary.updated, *summary.dropped)}
    remaining = [f for f in dreams_facts if f not in gone]
    memory_io.write_dreams_facts(remaining, cfg.engine.dreams_md_max_bytes)
    return summary


def _agent_profile_homes() -> list[Path]:
    """Gallery-agent profile home dirs (``$HERMES_HOME/agent-profiles/<slug>``)
    that have their own ``state.db`` — i.e. agents that have actually been chatted
    with. Dreaming iterates these so each specialized agent's OWN conversations are
    distilled into ITS membrane, instead of only the global store (the per-profile
    dreaming gap). Enumerated against the GLOBAL home; the trigger runs outside any
    HERMES_HOME override, so ``get_hermes_home()`` here is the real base.
    """
    try:
        from hermes_constants import get_hermes_home

        profiles_dir = get_hermes_home() / "agent-profiles"
    except Exception:  # noqa: BLE001
        return []
    out: list[Path] = []
    try:
        for child in sorted(profiles_dir.iterdir()):
            if child.is_dir() and (child / "state.db").exists():
                out.append(child)
    except Exception:  # noqa: BLE001
        pass
    return out


async def run_dream_cycle_all_profiles(*, force: bool = False) -> list["DreamRunSummary"]:
    """Run a dream cycle for the global home AND each gallery-agent profile.

    Each profile cycle is scoped to that profile's ``state.db`` + ``MEMORY.md``
    via the ``HERMES_HOME`` ContextVar override (which both ``default_state_db_path``
    and ``memory_io`` honor). The user's GLOBAL dreaming config is resolved once
    (no override) and passed to every cycle, so enabling dreaming once applies to
    all of that user's agents. The override is a per-task ContextVar, so this never
    races concurrent gateway turns.
    """
    summaries: list[DreamRunSummary] = []
    # Resolve the user's dreaming config from the global scope (no override) and
    # reuse it for every profile so per-profile config gaps don't silently disable.
    try:
        global_cfg = load_dreaming_config()
    except Exception:  # noqa: BLE001
        global_cfg = None

    # 1) Global / default profile (no override).
    try:
        summaries.append(await run_dream_cycle(force=force, config=global_cfg))
    except Exception as exc:  # noqa: BLE001
        logger.debug("dreaming: global cycle error: %s", exc)

    # 2) Each gallery-agent profile, scoped via the home override.
    homes = _agent_profile_homes()
    if not homes:
        return summaries
    try:
        from hermes_constants import (
            set_hermes_home_override,
            reset_hermes_home_override,
        )
    except Exception:  # noqa: BLE001
        return summaries
    for home in homes:
        token = None
        try:
            token = set_hermes_home_override(str(home))
            summaries.append(await run_dream_cycle(force=force, config=global_cfg))
        except Exception as exc:  # noqa: BLE001
            logger.debug("dreaming: profile cycle error (%s): %s", home.name, exc)
        finally:
            if token is not None:
                try:
                    reset_hermes_home_override(token)
                except Exception:  # noqa: BLE001
                    pass
    return summaries


def maybe_run_in_background(*, force: bool = False) -> None:
    """Fire-and-forget a dream cycle on a daemon thread. Never blocks the caller.

    Used by the ``on_session_start`` hook: dreaming must never delay a user's
    turn, so it runs in its own thread with its own event loop. Runs the global
    store AND every gallery-agent profile (each scoped to its own db/membrane).
    """
    def _worker() -> None:
        if not _run_lock.acquire(blocking=False):
            return  # a cycle is already running; skip this trigger
        try:
            import asyncio

            asyncio.run(run_dream_cycle_all_profiles(force=force))
        except Exception as exc:  # noqa: BLE001 — background; never surface
            logger.debug("dreaming: background cycle error: %s", exc)
        finally:
            _run_lock.release()

    t = threading.Thread(target=_worker, name="dreaming-cycle", daemon=True)
    t.start()
