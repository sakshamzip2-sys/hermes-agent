"""Phase 2 — one-way cross-feed: pull upstream dreamer outputs into local MEMORY.md.

Topology is strictly **one-way: Honcho -> GBrain -> local**. Nothing flows back
upward. We pull NEW, high-confidence, provenance-bearing outputs from the upstream
dreamers and offer them as MEMORY.md *candidates*:

* Honcho conclusions  (REST ``/v3/workspaces/<ws>/conclusions/list``)
* GBrain facts         (HTTP MCP ``recall`` over the ``facts`` table, ``kind='fact'``)

Each candidate is:

1. **Namespaced + provenance-tagged** — ``(dreamed YYYY-MM-DD · honcho#<id> · conf=high) <text>``
   so its origin is always legible in MEMORY.md.
2. **Run through the EXISTING local diversity gate** (``plugins.dreaming``) before
   promotion — never bypasses the gate that keeps MEMORY.md de-duplicated.
3. **Capped** by ``max_imports_per_run`` and floored at ``confidence_floor``.

HARD INVARIANT (no recursion -> no model collapse): every imported/derived line is
recorded in the orchestrator's ``imported`` ledger AND carries the provenance
marker. :func:`is_derived_line` recognises that marker so the LOCAL dreamer can
exclude these lines from its own candidate pool on subsequent runs. Imported facts
therefore never feed back into the dreamer that would re-dream and amplify them.

DEFAULT ``cross_feed.dry_run: true`` — the first runs only preview; nothing is
written until a user opts in.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger("hermes.plugins.dream_orchestrator.importer")

# Provenance marker that tags an imported/derived MEMORY.md line. The LOCAL
# dreamer excludes any candidate matching this so derived facts never recurse.
# Example: "(dreamed 2026-06-17 · honcho#abc123 · conf=high) The user ..."
_PROVENANCE_RE = re.compile(
    r"\(dreamed\s+\d{4}-\d{2}-\d{2}\s*·\s*(?:honcho|gbrain)#[^\s·)]+\s*·\s*conf=\w+\)",
    re.IGNORECASE,
)

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def is_derived_line(text: str) -> bool:
    """True if *text* is a cross-fed (imported) MEMORY.md line.

    Used to enforce the no-recursion invariant: the local dreamer must EXCLUDE
    these from its candidate pool so an imported conclusion is never re-dreamed.
    """
    return bool(_PROVENANCE_RE.search(text or ""))


@dataclass
class ImportCandidate:
    source: str          # "honcho" | "gbrain"
    ref: str             # upstream id, for the provenance tag + ledger key
    text: str            # the bare fact/conclusion text
    confidence: str = "high"

    @property
    def import_id(self) -> str:
        norm = " ".join((self.text or "").lower().split())
        return hashlib.sha256(f"{self.source}|{self.ref}|{norm}".encode()).hexdigest()[:16]

    def provenance_line(self) -> str:
        today = _dt.date.today().isoformat()
        return f"(dreamed {today} · {self.source}#{self.ref} · conf={self.confidence}) {self.text.strip()}"


@dataclass
class ImportSummary:
    previewed: list[str] = field(default_factory=list)   # provenance lines (dry-run or pre-gate)
    promoted: list[str] = field(default_factory=list)    # actually written to MEMORY.md
    queued_review: list[str] = field(default_factory=list)  # held in the HMAC review queue (review_mode)
    skipped_existing: int = 0                             # already imported (ledger) or in MEMORY
    dropped_diversity: int = 0                            # failed local diversity gate
    withheld_threat: int = 0                              # dropped: hit a strict-scope threat pattern
    redacted: int = 0                                     # secrets stripped before any write
    dry_run: bool = True

    def to_dict(self) -> dict:
        return {
            "previewed": self.previewed,
            "promoted": self.promoted,
            "queued_review": self.queued_review,
            "skipped_existing": self.skipped_existing,
            "dropped_diversity": self.dropped_diversity,
            "withheld_threat": self.withheld_threat,
            "redacted": self.redacted,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Upstream fetchers (best-effort; return [] on any failure)
# ---------------------------------------------------------------------------
def fetch_honcho_conclusions(limit: int = 50) -> list[ImportCandidate]:
    try:
        import httpx

        from .targets import _honcho_config

        base_url, ws, _peer, api_key, enabled = _honcho_config()
        if not base_url or not enabled:
            return []
        url = f"{base_url}/v3/workspaces/{ws}/conclusions/list"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        r = httpx.post(url, json={}, headers=headers, timeout=30.0)
        if r.status_code != 200:
            logger.debug("honcho conclusions/list HTTP %s", r.status_code)
            return []
        items = (r.json() or {}).get("items", [])
        out: list[ImportCandidate] = []
        for it in items[:limit]:
            text = (it.get("content") or "").strip()
            cid = str(it.get("id") or "")
            if text and cid:
                # Honcho conclusions are server-derived; treat as high-confidence.
                out.append(ImportCandidate("honcho", cid, text, "high"))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("honcho conclusions fetch failed: %s", exc)
        return []


def fetch_gbrain_facts(limit: int = 50) -> list[ImportCandidate]:
    try:
        from .targets import _gbrain_rpc, _gbrain_token

        token = _gbrain_token()
        if not token:
            return []
        obj = _gbrain_rpc("tools/call",
                          {"name": "recall", "arguments": {"limit": min(limit, 100)}},
                          token=token, timeout=30.0)
        if "error" in obj:
            return []
        content = obj.get("result", {}).get("content", [])
        if not content:
            return []
        import json as _json

        try:
            payload = _json.loads(content[0].get("text", "{}"))
        except (TypeError, ValueError):
            return []
        facts = payload.get("facts", []) if isinstance(payload, dict) else []
        out: list[ImportCandidate] = []
        for f in facts[:limit]:
            text = (f.get("text") or f.get("content") or "").strip()
            fid = str(f.get("id") or f.get("fact_id") or "")
            if text and fid:
                conf = str(f.get("confidence") or "high").lower()
                if conf not in _CONF_RANK:
                    conf = "high"
                out.append(ImportCandidate("gbrain", fid, text, conf))
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("gbrain facts fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Cross-feed driver
# ---------------------------------------------------------------------------
def run_cross_feed(cfg, store) -> ImportSummary:
    """Pull upstream outputs -> namespace -> diversity-gate -> (optionally) promote.

    *cfg* is a :class:`~plugins.dream_orchestrator.config.CrossFeedConfig`.
    *store* is the :class:`~plugins.dream_orchestrator.store.OrchestratorStore`
    (for the import idempotency ledger).
    """
    summary = ImportSummary(dry_run=cfg.dry_run)
    floor = _CONF_RANK.get(str(cfg.confidence_floor).lower(), 2)

    # Topology order: honcho first, then gbrain (one-way honcho -> gbrain -> local).
    candidates = fetch_honcho_conclusions() + fetch_gbrain_facts()

    already = store.imported_ids()
    # Local MEMORY.md corpus for the diversity gate (markers stripped).
    try:
        from plugins.dreaming import memory_io

        existing = memory_io.read_memory_facts()
    except Exception:  # noqa: BLE001
        memory_io = None  # type: ignore[assignment]
        existing = []

    selected: list[ImportCandidate] = []
    for c in candidates:
        if _CONF_RANK.get(c.confidence, 0) < floor:
            continue
        if c.import_id in already:
            summary.skipped_existing += 1
            continue
        # Don't re-import a fact whose bare text already lives in MEMORY.md.
        if any(c.text.strip() == e.strip() for e in existing):
            summary.skipped_existing += 1
            continue
        selected.append(c)
        if len(selected) >= cfg.max_imports_per_run:
            break

    if not selected:
        return summary

    # Diversity gate: reuse the local dreamer's embedding + threshold so imports
    # don't duplicate existing memories. Degrades to "novel" if embeddings are
    # unavailable (same posture as the local dreamer).
    diversity_threshold, embed_fn = _local_diversity()

    for c in selected:
        # SAFETY FENCE (req #2/#3): a fetched cross-feed line is untrusted input
        # destined for the always-injected MEMORY.md. Threat-scan + redact it
        # BEFORE it can be previewed, queued, or promoted. A line that hits a
        # strict-scope threat pattern is WITHHELD entirely (never written).
        safe_text, was_redacted = _fence_line(c.text)
        if safe_text is None:
            summary.withheld_threat += 1
            # Ledger withheld lines (live runs) so a poisoned upstream row isn't
            # re-scanned every cycle; additive + reversible (no DROP/DELETE).
            if not cfg.dry_run:
                store.mark_imported(c.import_id, source=c.source, ref=c.ref)
            continue
        if was_redacted:
            summary.redacted += 1
        # Rebuild the candidate from the sanitised + redacted text so the
        # provenance line that lands in MEMORY.md carries no secret.
        safe_c = ImportCandidate(c.source, c.ref, safe_text, c.confidence)
        line = safe_c.provenance_line()
        summary.previewed.append(line)
        if memory_io is None:
            continue
        try:
            keep = _passes_diversity(safe_c.text, existing, embed_fn, diversity_threshold)
        except Exception as exc:  # noqa: BLE001
            logger.debug("diversity check failed (%s); treating as novel", exc)
            keep = True
        if not keep:
            summary.dropped_diversity += 1
            # Still ledger it so we don't re-evaluate a known-duplicate each run.
            if not cfg.dry_run:
                store.mark_imported(safe_c.import_id, source=safe_c.source, ref=safe_c.ref)
            continue
        if cfg.dry_run:
            continue
        if cfg.review_mode:
            # review_mode (default): a scanned-clean + redacted line is a PROPOSAL,
            # not an auto-write. Queue it into the dreaming HMAC review queue so an
            # operator accepts/rejects it via `hermes dream review`. MEMORY.md is
            # left untouched here (additive, reversible — req #2/#3).
            queued = _queue_for_review(line, safe_c)
            if queued:
                existing.append(safe_c.text)  # keep the in-run corpus current
                store.mark_imported(safe_c.import_id, source=safe_c.source, ref=safe_c.ref)
                summary.queued_review.append(line)
            continue
        # review_mode off (back-compat): promote the PROVENANCE LINE verbatim
        # (promote_raw, NOT promote) so the marker travels intact into MEMORY.md
        # (the local dreamer excludes it later) WITHOUT promote() prepending a
        # second "(dreamed …)" prefix.
        memory_io.promote_raw(line)
        existing.append(safe_c.text)  # keep the in-run corpus current
        store.mark_imported(safe_c.import_id, source=safe_c.source, ref=safe_c.ref)
        summary.promoted.append(line)

    return summary


def _queue_for_review(line: str, candidate: ImportCandidate) -> bool:
    """Queue a scanned-clean cross-feed line into the dreaming HMAC review queue.

    Returns True on success. The provenance ``line`` is what an operator would
    accept into MEMORY.md (carrying the cross-feed marker so the local dreamer
    still excludes it). Fail-soft: a queue error returns False and the caller
    leaves the line un-ledgered so it can be retried next run.
    """
    try:
        from plugins.dreaming import review
        from plugins.dreaming.runner import _review_home

        review.queue_pending(
            _review_home(),
            text=line,
            source_event_id=f"crossfeed:{candidate.source}#{candidate.ref}",
            score=_CONF_RANK.get(candidate.confidence, 2) / 2.0,
            recall_count=0,
            diversity_score=0.0,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("cross-feed: review queue failed for a line: %s", exc)
        return False


def _fence_line(raw_text: str) -> tuple[str | None, bool]:
    """Threat-scan + redact a fetched cross-feed line before it can reach MEMORY.md.

    Returns ``(safe_text, redacted)``:

    * ``safe_text is None`` -> WITHHELD: the line hit a strict-scope threat
      pattern (injection / promptware / persistence / exfil) and must NOT be
      written or queued.
    * otherwise ``safe_text`` is the sanitised + secret-redacted text, and
      ``redacted`` is True when at least one secret was stripped.

    The scan mirrors the two write-path fences elsewhere in the engine:
    ``sanitize_context`` (strip injected context/fence tags) then
    ``scan_for_threats(scope="strict")`` (the broad, user-mediated-write set).
    Fail-CLOSED: if the fence modules cannot be imported we WITHHOLD rather than
    write an unscanned line into the always-injected MEMORY.md.
    """
    try:
        from agent.memory_manager import sanitize_context
        from tools.memory_redaction import redact
        from tools.threat_patterns import scan_for_threats
    except Exception as exc:  # noqa: BLE001 — fail closed, never write unscanned
        logger.warning("cross-feed fence unavailable (%s); withholding line", exc)
        return None, False

    cleaned = sanitize_context(raw_text or "")
    findings = scan_for_threats(cleaned, scope="strict")
    if findings:
        logger.warning("cross-feed: WITHHELD a fetched line (threat: %s)", ",".join(findings))
        return None, False
    safe, hits = redact(cleaned)
    return safe, bool(hits)


def _local_diversity():
    """Return ``(diversity_threshold, embed_fn)`` from the local dreamer."""
    try:
        from plugins.dreaming import llm
        from plugins.dreaming.config import load_dreaming_config

        cfg = load_dreaming_config()
        return cfg.engine.diversity_threshold, llm.semantic_embed
    except Exception:  # noqa: BLE001
        return 0.8, None


def _passes_diversity(text: str, existing: list[str], embed_fn, threshold: float) -> bool:
    """True if *text* is NOT a near-duplicate of any existing MEMORY.md entry."""
    from plugins.dream_orchestrator.targets import _run_coro
    from plugins.dreaming.engine import best_match_against

    if not existing or embed_fn is None:
        return True
    # Loop-safe: this runs inside the dream cycle, which itself may run on a
    # worker-thread loop when invoked from the gateway — a bare asyncio.run here
    # would raise "cannot be called from a running event loop".
    diversity, _idx = _run_coro(best_match_against(text, existing, embed_fn=embed_fn))
    return diversity < threshold
