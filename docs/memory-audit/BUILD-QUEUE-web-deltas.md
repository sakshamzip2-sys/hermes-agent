# Build Queue: deltas from the web-grounded validation

Actionable contract distilled from `WEB-VALIDATION-verdict.md` (which has the full evidence +
citations). The verdict confirmed our architecture is on the 2026 production mainline; these are
the gap-fills and one real safety correction. Ordered by priority. No em dashes.

## SAFETY-CRITICAL (do first, before remote planes go live)

1. **Floor only provenance-TRUSTED sources (the A-MemGuard correction).** Our Wave-2 P1 fix made
   `per_source_floors` always protect a sole-source plane. That is the inverse of safe: a
   poisoned single Honcho/GBrain/cross-fed row is exactly the un-corroborated outlier
   A-MemGuard suppresses (ASR 100 to 2.13). FIX: floors protect ONLY user-authored and
   signed-self-generated sources; bulk-auto-captured / cross-fed / external rows are never
   floor-protected and are subject to a retrieval-time consensus check. File: `agent/memory_merge.py`
   (before slot allocation), config `memory.merge.per_source_floors`. Source: A-MemGuard
   arXiv 2510.02373.

2. **Tamper-evident provenance (not a forgeable string tag).** Add a per-fact SHA-256 integrity
   hash + an agent-self-signature (reuse the HMAC key the dreaming review queue already holds).
   Unsigned / externally-sourced facts get a hard trust ceiling and never reach the
   always-injected tier or floor protection. File: `plugins/memory/holographic/store.py` schema
   (folds into the bi-temporal migration) + the MergeLayer trust step. Source: MemoryGraft
   arXiv 2512.16962 (implants "validated best practices", no trigger phrase).

3. **Retrieval-time consensus / contrastive check.** Cross-check a retrieved entry against
   parallel reasoning paths from related memories; suppress the un-corroborated outlier. File:
   `agent/memory_merge.py`. Source: A-MemGuard.

4. **Ship the dream-ingest fence + cross-feed review_mode NOW.** `importer.run_cross_feed` writes
   UNSCANNED lines into the always-injected MEMORY.md with `dry_run:false` the moment Honcho /
   GBrain return. Live hole today. Gate it behind the per-plane scan + review_mode before the
   servers come back. File: `plugins/dream_orchestrator/importer.py`.

5. **Weak-signal injection test suite.** Our req-#11 test only stores strong-signal payloads;
   weak-signal attacks (policy-conformant fabricated fact, false-precedent task log, a MemoryGraft
   "successful experience", a MINJA query-only induced write) walk straight through. Assert each
   is NOT promoted, NOT floor-protected, and caught by the consensus/provenance check. Source:
   MemoryGraft, MINJA.

## EVAL HARDENING (Wave 1 sizing)

6. **Grow the gold set to >=40 labeled query-relevance pairs** (from ~20) so it doubles as
   calibration data. Add BEAM case classes: contradiction-resolution, event-ordering (as a PROBE
   not a gate; all systems score ~19.5% there), and a knowledge-update-vs-contradiction
   discrimination case (do not cry-contradiction on a metric that simply moved over time).
   Source: BEAM arXiv 2510.27246.

7. **Add a k-sweep {20,30,40,60} to the eval.** RRF is k-sensitive for short lists (we take top-8
   from small lists, where k in [10,40] sharpens vs k=60 flattening); the folklore "k=60 always"
   is wrong here. Slate convex-combination fusion behind `memory.merge.fusion: {rrf|convex}` once
   the gold set yields labels. Source: ACM TOIS arXiv 2210.11934.

## REMOTE-PLANES PLAN (Wave 7, Honcho consumption from the Nous doc)

8. **`honcho.recallMode: 'tools'`** (NOT the default `hybrid`, which auto-injects a second memory
   block OUT OF BAND of the MergeLayer = double-injection, un-fused, un-deduped). In `tools` mode
   the MergeLayer calls `get_context` explicitly and feeds Honcho's items into RRF like any plane.
9. **Consume `get_context` synchronously (~200ms hybrid read, no LLM); NEVER `peer.chat()` on the
   hot path.** Reserve `peer.chat()`/Dialectic for the model-elected `memory_search` meta tool,
   gated behind the same cost tier as the cross-encoder reranker.

## NEXT

10. **Cheap local entity-match channel folded into RRF** (a small SQLite entity index of proper
    nouns / IDs / quoted spans). Gives Mem0's entity-fusion win even when GBrain is down. Mem0 v3
    dropped the external graph and folded entities into the unified store. Source: Mem0 v2-to-v3.
11. **Split the source-tier prior: add a ~0.75 agent-confirmed tier** (do not lump load-bearing
    agent decisions with bulk chat at 0.5); let outcome band drive demotion. Source: Mem0 2026.
12. **Supervisor: post-write behavioral-drift monitor** (bounded variance of promoted-fact
    confidence; the Zombie self-reinforcement fingerprint), not just the nightly eval.

## DEFER

13. Git-versioned memory projection as an optional default-OFF audit trail on promotion (Letta
    Context Repositories) - NOT a reversal of the C3 system-of-record rejection.
14. Temporal compression/abstraction at scale (BEAM shows a degradation regime past ~1M).

## What the verdict VALIDATED (do not change)

RRF fan-out spine; bi-temporal invalidate-don't-delete (= Zep's 15-point LongMemEval win, and we
are already SAFER than Mem0 which retreated from destructive UPDATE/DELETE in 2026); single-level
background consolidation; outcome-gated promotion; two-layer eval (the LoCoMo judge audit
vindicates the frozen repo-local gold set); FTS5 backbone + NL to OR; Honcho as identity-only.
