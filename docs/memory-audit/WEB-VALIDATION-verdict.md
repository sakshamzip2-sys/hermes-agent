# Web-Grounded Validation Verdict: OpenComputer v2 Memory Architecture

## 1. Verdict

Your architecture is **sound and on the 2026 production mainline, not behind it**: parallel fan-out + RRF + source-tier prior + per-source floors is now the independently-converged default retrieval shape (Mem0's 3-pass fuse, Zep's semantic+BM25+graph, Hindsight's 4-way RRF), your bi-temporal invalidate-don't-delete is byte-for-byte Zep/Graphiti's measured 15-point-winning design, your background reconcile-in-dreaming matches Letta sleep-time + [Anthropic Dreaming](https://letsdatascience.com/blog/anthropic-dreaming-claude-managed-agents-self-improving-may-6) (a single-level managed-agent consolidation, exactly the flatten the federated-dreaming verdict already called), and your two-layer eval is the correct response to the LoCoMo judge-unreliability audit. The single most important change is the one place the field has moved past you on **safety, not architecture**: your per-source floors structurally protect exactly the un-corroborated sole-source candidate that [A-MemGuard (arXiv 2510.02373)](https://arxiv.org/abs/2510.02373) proves you must instead *suppress*, and your entire injection defense is signal/pattern-based while the 2026 attacks ([MemoryGraft 2512.16962](https://arxiv.org/abs/2512.16962), weak-signal fact injection) carry no syntactic anomaly. **BUILD-NOW: a retrieval-time consensus/contrastive check plus tamper-evident provenance (SHA-256 + agent-self-signature), reconciled so floors protect only provenance-trusted sources.** Everything else is sequencing and gap-filling.

---

## 2. VALIDATED (with citations)

- **Parallel fan-out + RRF k=60 spine.** Confirmed as the cross-vendor default (Elastic, OpenSearch, [Azure AI Search](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking)) and independently re-derived by [Mem0 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) (three parallel scoring passes fused) and Hindsight (4-way RRF). Your MergeLayer is mainstream-correct, not exotic.
- **Bi-temporal invalidate-don't-delete (t_valid/t_invalid/supersedes_id, recency-wins).** This is Zep/Graphiti's exact model, and the 15-point LongMemEval gap (Zep 63.8% vs Mem0 49.0%, [arXiv 2501.13956](https://arxiv.org/abs/2501.13956)) is attributed entirely to it. Doubly validated: it is also the literature's model-collapse mitigation ("accumulation prevents collapse, replacement causes it").
- **You are already SAFER than the system you cite as the write contract.** [Mem0 publicly retreated from destructive UPDATE/DELETE reconcile in 2026](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm) (single-pass ADD-only; overwrites destroyed chronological context, the two-dogs bug). You already `invalidate()`-not-delete with recency-wins supersede, landing on the safe Zep side of the one axis the field corrected.
- **Background consolidation off the hot path, single-level.** Validated by Letta sleep-time and [Anthropic Dreaming](https://www.buildfastwithai.com/blogs/claude-managed-agents-dreaming-explained) (one managed agent, reads up to 100 sessions, removes duplicates + outdated entries, preserves transcripts). Nobody runs recursive per-leaf dreamers; your federated-dreaming flatten verdict is fully frontier-validated.
- **Outcome-gated promotion is the genuinely-new defensible primitive.** Anthropic explicitly pairs Dreaming with an Outcomes rubric in a separate context window; Harvey reported ~6x completion gains "when paired with a tight outcomes rubric, so any drift in memory would be caught by the grader on the next run." Memory-R1 ([arXiv 2508.19828](https://arxiv.org/abs/2508.19828)) trains ADD/UPDATE/DELETE/NOOP with outcome-driven RL.
- **Two-layer eval (deterministic CI gate + nightly LLM-judge, different model, stratified).** Vindicated by the LoCoMo audit (6.4% answer-key errors; judge accepted 62.81% of intentionally-wrong answers). Your frozen repo-local gold set instead of trusting LoCoMo is the correct response.
- **Keyword/BM25 (FTS5) backbone + NL→OR expansion.** Lexical search is the non-negotiable channel for exact strings (paths, keys, IDs); dense pooling causes semantic blurring. Your recall_probe.py 0.62→1.00 fix is the documented win.
- **Honcho consumed as identity/representation only, raw turns in, let the Deriver synthesize.** Correct per Honcho's Storage/Insights split; `get_context` is a ~200ms hybrid FTS+vector read, not an LLM call.

---

## 3. CHANGE (specific design changes, each with evidence + the file/wave it touches)

1. **Add a retrieval-time consensus/contrastive check, and reconcile it against per-source floors.** This is the headline change. [A-MemGuard (arXiv 2510.02373)](https://arxiv.org/pdf/2510.02373) cuts attack success >95% (ASR-at-retrieval 100.0 → 2.13 on EHRAgent) by cross-checking a retrieved entry against parallel reasoning paths from related memories and suppressing the un-corroborated outlier. Your `per_source_floors: true` does the **inverse** — it guarantees a sole-source candidate is never buried, which is exactly the poisoned single Honcho identity row or single cross-fed GBrain row A-MemGuard wants suppressed. Resolve the collision by floor-protecting **only provenance-trusted sources** (user-authored, signed-self-generated), never bulk-auto-captured or cross-fed content. Touches: `agent/memory_merge.py` (the MergeLayer, before slot allocation), config `memory.merge.per_source_floors`. Wave 3.

2. **Make provenance tamper-evident, not a string tag.** [MemoryGraft (arXiv 2512.16962)](https://arxiv.org/html/2512.16962v1) implants malicious "successful experiences" framed as validated best practices, activates by semantic similarity with no trigger phrase, and exploits imitation. A `source_store` namespace string is forgeable; a signature is not. Add a per-fact SHA-256 integrity hash + an agent-self-signature (reuse the HMAC key the dreaming review queue already holds). Unsigned/externally-sourced facts get a hard trust ceiling and never reach the always-injected tier or floor protection. Touches: `plugins/memory/holographic/store.py` schema (Wave 5, folds into the bi-temporal migration), MergeLayer trust step.

3. **Make UPDATE a soft-supersede everywhere, and re-cite the rationale.** [Mem0's 2026 ADD-only retreat](https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm) (+42.1 temporal from preserving chronological context) confirms in-place overwrite is a net negative. Your reconcile engine still emits UPDATE/DELETE ops; on a knowledge-update, INSERT new + `invalidate()` old (set t_invalid, link supersedes_id), never overwrite. Reserve hard DELETE strictly for redaction/GC behind the backup gate. Update Phase 2/3 prose to cite **Zep supersede**, not Mem0's deprecated destructive state machine. Touches: the reconcile engine in `plugins/dreaming/`. Wave 6.

4. **Demote "RRF only, no score blending" to an explicit phase-1 default, and A/B the k.** The peer-reviewed [ACM TOIS fusion analysis (arXiv 2210.11934)](https://arxiv.org/abs/2210.11934) shows tuned convex combination (TM2C2) beats RRF in-domain AND out-of-domain with one easy-to-tune parameter, and that **RRF is itself k-sensitive** (contradicting the folklore you lean on). Your regime is top-8 from small lists (short-list, where k∈[10,40] sharpens vs k=60 flattening). Add a one-line k-sweep {20,30,40,60} to the gold-set eval; slate convex fusion behind `merge.fusion: {rrf|convex}` once the gold set yields labels. Touches: eval harness + `memory_merge.py`. Wave 1 (sizing) + Wave 3.

5. **Move the local reranker into the spine as a measured A/B; stop gating it to synthesis-only.** Hindsight (BEAM SOTA) always cross-encoder-reranks; Anthropic Contextual Retrieval data shows the rerank stage cuts top-20 failure another ~35% relative (2.9%→1.9%); bge-base is ~92ms p95, FlashRank ~50ms/20 docs — no credit, no server. Reframe from "default-OFF, synthesis-path-only" to "on for the always-on path unless latency-critical," with the eval measuring the recall@5/precision delta. Touches: config `memory.merge.rerank`, MergeLayer. Wave 3.

---

## 4. ADD (things the leaders ship that you lack, prioritized)

**BUILD-NOW**
- **Retrieval-time consensus check + tamper-evident provenance** — see CHANGE 1 and 2. Source: [A-MemGuard](https://arxiv.org/abs/2510.02373), [MemoryGraft](https://arxiv.org/abs/2512.16962).
- **Contradiction-resolution + event-ordering case classes in the frozen gold set, plus a knowledge-update-vs-contradiction discrimination case.** [BEAM (ICLR 2026, arXiv 2510.27246)](https://mohammadtavakoli78.github.io/beam-light/) adds exactly these and they are precisely what your bi-temporal supersede is *for*, yet no test exercises them. **Caveat from the live data: BEAM event-ordering scores only 19.5% across all current systems** (vs contradiction-resolution 91.4%) — so set the event-ordering bar as a *probe*, not a pass/fail gate, and do not overclaim the bi-temporal mechanism. The discrimination case enforces "do not cry-contradiction on a metric that simply moved over time." Source: [BEAM](https://mohammadtavakoli78.github.io/beam-light/).
- **A weak-signal / poisoned-experience injection test suite** (Policy-Conformant fabricated fact, False-Precedent task log, a MemoryGraft "successful experience" encoding force-push/skip-validation, a MINJA query-only induced write), asserting each is NOT promoted, NOT floor-protected, and caught by the consensus/provenance check rather than the keyword scanner. Your current req-#11 test only stores strong-signal payloads, which weak-signal attacks walk straight through. Source: [MemoryGraft](https://arxiv.org/html/2512.16962v1).
- **Ship dream-ingest fence (amplification #8) and cross-feed review_mode (#7) now, before servers return** — `importer.run_cross_feed` writes unscanned lines into the always-injected MEMORY.md with `dry_run:false` the moment Honcho/GBrain return. This is a live hole today.
- **Size the gold set to ≥40 labeled query-relevance pairs, not ~20**, so it doubles as calibration data for convex-combination alpha and the k-sweep. Source: [ACM TOIS](https://arxiv.org/abs/2210.11934).

**NEXT**
- **A cheap local entity-match channel folded into RRF.** [Mem0's 2026 move](https://docs.mem0.ai/migration/oss-v2-to-v3) was to drop the external graph store and fold entity-linking into the unified store ("the relations field is no longer populated; entity relationships consumed via retrieval ranking"). A small SQLite entity index (proper nouns / IDs / quoted spans) gives you Mem0's entity-fusion win even when GBrain is down. Source: [Mem0 migration v2→v3](https://docs.mem0.ai/migration/oss-v2-to-v3).
- **Split the source-tier prior so agent-confirmed facts are first-class.** Mem0 2026 elevated agent confirmations to equal weight with user-stated facts; your flat 0.5x lumps load-bearing agent decisions with bulk chat. Add a ~0.75 tier and let outcome band (which DREAMING #2 already persists) drive the demotion, not source-tier alone. Source: [Mem0 State of 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026).
- **Post-write / behavioral-drift monitoring in the Memory Supervisor** — watch for anomalous trust-weight growth on a single fact lineage (the Zombie self-reinforcement fingerprint); wire the "bounded variance of promoted-fact confidence" check into the running supervisor, not just the nightly eval.
- **An external LoCoMo/LongMemEval adapter** so you can place yourself on the public leaderboard and defend recall claims. Source: [Mem0 benchmarks 2026](https://mem0.ai/blog/ai-memory-benchmarks-in-2026).

**DEFER**
- **Git-versioned memory projection.** [Letta Context Repositories (Feb 2026)](https://www.letta.com/blog/context-repositories/) solve subagent divergence via git merge — the exact problem your Decision B promotion hand-rolls. Worth re-opening as an OPTIONAL default-OFF audit-trail projection on promotion, **not** reversing the C3 system-of-record rejection (which the BEAM/Mem0 evidence still supports). Source: [Letta](https://www.letta.com/blog/context-repositories/).
- **Temporal compression/abstraction at scale.** BEAM shows a sharp degradation regime (temporal reasoning and event-ordering drop while knowledge-update *improves* 77.6%→90.0% from 1M→10M). Your bi-temporal substrate handles correctness-at-small-scale; note temporal abstraction as a deferred scale item. Source: [BEAM](https://mohammadtavakoli78.github.io/beam-light/).

---

## 5. Honcho Consumption + Bring-Up Plan (from the Nous doc, verbatim defaults verified)

Confirmed live against the [Nous Hermes honcho.md config table](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/honcho.md). Set these in `memory.merge.honcho.*` for the remote-planes wave (Wave 7):

- **`recallMode: 'tools'`** — the default is `'hybrid'`, which AUTO-INJECTS a second memory block into the system prompt **out of band of your MergeLayer** (double-injection, un-fused, un-deduped). Set `'tools'` so Honcho stops auto-injecting; the MergeLayer then calls `get_context` explicitly and feeds its FTS+vector-ranked items into RRF like any other plane, under your ranking/dedup/abstention control. (`'context'` is the fallback if you still want an auto-block.)
- **Consume `get_context` synchronously, NEVER `peer.chat()`.** `get_context` is the cheap ~200ms hybrid read (no LLM); `peer.chat()`/Dialectic is the expensive tool-using loop. Reserve `peer.chat()` for the model-elected `memory_search` meta tool only, gated behind the same cost tier as the cross-encoder reranker. Your single "expensive remote plane" label conflates the two — fix the cost framing.
- **`contextTokens`: set a hard cap 800–1200.** The default is **`null` = UNCAPPED** — the single biggest unbounded-cost footgun. Never leave it null.
- **`dialecticCadence: 3-5`** (raise from default `2`, recommended range 1-5), **`dialecticDepth: 1`** (default), **`dialecticMaxChars: 600`** (keep default), **`reasoningHeuristic: true`** (default).
- **`sessionStrategy`: pin `'per-session'` for the eval harness / test profiles** so cross-run accumulation cannot make fused-recall tests flaky. The Hermes default is **`'per-directory'`** (cross-run continuity per working dir); decide per-product for main chat and state it explicitly rather than inheriting the default silently.
- **Bring up cheaply:** run the **deriver as a separate persistent process** ([Honcho issue #494](https://github.com/plastic-labs/honcho/issues/494) documents representations staying empty until it drains), keep `writeFrequency: 'async'` + `saveMessages: true` (defaults), call only `get_context` on the read path. Recurring LLM cost is then the deriver batch (off hot path) plus any `peer.chat` — a near-zero-marginal-cost recall plane.
- **Make the eval deriver-aware (closes O-7):** after writing turns, poll the queue-status endpoint for `completed_work_units >= expected` before asserting cross-store fused recall, with a keyword-search fallback and bounded timeout, so async-derivation lag cannot produce a false RED.
- **Validate the server version first (O-4):** the queue-status / peer-card / `recallMode` semantics above are Honcho 3.x; you pin SDK 2.0.1 against server 3.0.9. Pin the server to the SDK or upgrade the SDK before depending on the deriver-lag polling.

---

## 6. GBrain Role: Separate Plane or Fold In — Decided

**Decision: keep GBrain as a narrow, on-demand, model-invoked + router-gated plane for now, but make the separate-Postgres-service decision DATA-GATED via a 2-week shadow measurement, with a pre-committed fold-in path.** The 2026 evidence does not say "every agent needs a graph" — it says graph earns its keep only on multi-hop relationship traversal and temporal as-of reasoning, a minority slice, and the cost lands on the **write** path (Graphiti-class ingestion: multiple LLM calls, ~500-2000 input + 200-800 output tokens per episode). The decisive data point: [Mem0's 2026 migration explicitly dropped the external graph store](https://docs.mem0.ai/migration/oss-v2-to-v3) (Neo4j/Memgraph removed; relations field no longer populated) and folded entity-linking into the unified store, calling it a net improvement for most teams. For a single-user personal agent the regulated/audit/cross-source-disambiguation constraints that justify a separate graph store are largely absent.

Concretely:
- **Do NOT promote GBrain to always-write-on-entity-intent.** Fire it in recall only for the router's multi-hop and temporal/as-of intent classes, always with parallel fallback; a missing GBrain contributes 0 to RRF.
- **Verify GBrain's write path is genuinely zero-LLM** before keeping always-queue-on-entity-intent; if it does any LLM extraction, batch writes (30-50% token savings) and gate by salience.
- **Add a RecallTrace field** tagging which intent class fired GBrain and whether it changed the fused top-k. If GBrain's sole/top-source hit-rate clears 10-15% of entity/temporal turns over 2 weeks, keep it; below that, **fold a local entity index into RRF** (the Mem0 move) and retire the separate crash-prone service.
- **Pre-commit a tight read deadline** (target sub-500ms contribution, fuse-whatever-returned) now from the literature prior (~0.83s p95 graph tax for ~1.5 accuracy points), rather than waiting for live p99. GBrain must never sit on the critical path for an interactive turn.
- **Do not build a precomputed GraphRAG community-summary layer** — diminishing returns vs cost; [LazyGraphRAG](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) shows the multi-hop benefit is capturable lazily at query time. Keep GBrain's value in entity resolution + temporal validity windows.

This also gives you a cheap local entity-match channel as insurance: you get Mem0's entity-fusion win even while GBrain's v2 server is down (its current state).

---

## 7. Updated Remaining-Wave Sequence (with cost caps)

The principle holds from PHASE3 section 6 and all three personas converge on it: **drive the LOCAL-only spine to a green eval gate; park only what costs money behind the credit gate.** Reordered to fold in the BUILD-NOW safety items and the free local reranker.

1. **Eval + gold set FIRST (zero LLM spend).** Size ≥40 labeled pairs. Expand the taxonomy with contradiction-resolution, event-ordering (probe not gate, per BEAM's 19.5%), knowledge-update-vs-contradiction discrimination, and weak-signal/poisoned-experience cases. Add the k-sweep {20,30,40,60}. Cost cap: $0.

2. **Local readonly + NL→OR expansion + leak-closure + write-path fence (parallel, disjoint files, free).** `search_facts_readonly` (kills C-1), lineage-scope `session_search` + threat-scan on return (closes the live C-4 leak), and **ship amplification #8 (redact+scan on dream ingest) + #7 (review_mode on cross-feed) now**. Cost cap: $0.

3. **MergeLayer over the two LOCAL planes + RecallTrace + local reranker A/B.** Add bge-base/FlashRank (no credit, no server) as a measured A/B decided by the gold-set delta. **Add the A-MemGuard consensus/contrastive check and reconcile it against per-source floors here** (floor-protect only provenance-trusted sources). Cost cap: $0 (local rerank model only).

4. **Bi-temporal schema + tamper-evident provenance + two-phase add_fact (backup-gated, pending O-2).** Promote this toward the front — it is the highest-ROI, most-evidenced piece (Zep +15, Mem0 +42.1 temporal) and the model-collapse mitigation. Fold in the SHA-256 hash + agent-self-signature from CHANGE 2. Cost cap: $0; **requires user go on O-2** (creating `memory_store.db` as the durable plane).

5. **Reconcile write engine (one-plane-per-fact, redaction, op-queue, UPDATE→soft-supersede).** Cite Zep supersede, not Mem0's deprecated reconcile. Read-your-writes hot FTS5 write. Cost cap: the reconcile LLM runs background-only, on a capable model, cadence `on_session_end` — bounded, off the hot path.

6. **GREEN gate checkpoint** — flip nothing live until fused recall@5 + MRR clear the frozen floor and the poisoning suite is green.

7. **Remote planes (BLOCKED on O-1 credits + O-3 p99s).** Bring Honcho up with the bounded config from section 5 written **now** (recallMode tools, contextTokens 800-1200, dialecticCadence 3-5, sessionStrategy pinned, deriver as separate process). Measure p99, set deadlines + wall-clock budget, wire the asymmetric router. GBrain enters under the data-gated decision in section 6. Cost cap: gate `peer.chat()`/Dialectic and GBrain writes behind the credit gate; `get_context` (~200ms, no LLM) and local planes stay free.

8. **Promotion (default OFF) + req-#10 promotion test + consequence-aware review.** Keep promotion OFF until the consensus check and weak-signal eval are green; require promoted experiences to carry a resolvable grounding pointer (hard gate), be signed, and pass a consequence-aware review ("does this precedent encode force-push/skip-validation/run-remote-script?") — not just a keyword scan, because turn_score alone is the survey's poisoned-confidence-calibration vector.

9. **Memory Supervisor + behavioral-drift monitor + flip `merge.enabled: true`.** Fail-open recall / fail-closed writes; wire the promoted-fact-confidence variance check into the running supervisor.

10. **Optional cost-tiered upgrades (default OFF, each measured):** convex-combination fusion once labels exist, A3 synthesis over the prose tier only, `memory_search` meta tool (the model-elected `peer.chat` deep-recall surface), external LoCoMo/LongMemEval adapter, git-versioned promotion projection.

Net: the local spine plus all BUILD-NOW safety work ships at **$0 marginal cost** behind a green eval gate; only Wave 7's `peer.chat`/Dialectic, GBrain writes, and the deriver batch sit behind O-1.
