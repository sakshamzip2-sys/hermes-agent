# Phase 3 - Locked Memory Design Decisions (OpenComputer v2)

Status: LOCKED. This is the converged design the design council produced after red-teaming
every candidate for the three decisions. It is the build contract for Phases 4 to 6. No em
dashes (house rule). Every load-bearing code claim below was re-verified against the real tree
at audit time (file:line cited inline).

This document inherits and does not restate: the Phase 1 verdict (v2 memory is
single-external-provider, the agent recall path is concat-only with no fusion/rerank/timeout,
holographic is a working-but-dormant SQLite+FTS5+HRR store, GBrain is MCP tools not a provider,
Honcho is the active-but-down provider, per-agent `agent-profiles/<slug>/state.db` isolation is
airtight), and the Phase 2 brief (parallel-fan-out + RRF + source-tier prior + per-source
floors + optional rerank; extract-and-reconcile one-plane-per-fact write; isolated-by-default
with gated promotion; two-layer eval).

A note on what the red-team changed. Every candidate came back `wounded` or `killed`, and the
common cause was the same four code realities that the seeds glossed over. These are now
first-class design constraints, verified live:

- **C-1 holographic `search_facts` WRITES on read.** `plugins/memory/holographic/store.py:228-238`
  runs `UPDATE facts SET retrieval_count = retrieval_count + 1 ... ; commit()` on every recall,
  under ONE `threading.RLock` over ONE shared `sqlite3.Connection(check_same_thread=False)`
  (`store.py:115-120`). The holographic plane is therefore NOT a free read; it is a
  lock-contending writer. Any design that treats it as a deadline-free synchronous backbone is
  wrong at the source.
- **C-2 `_rebuild_bank` is O(n) per write.** `store.py:498-534` re-reads and re-bundles EVERY
  `hrr_vector` in a category on every `add_fact`/`update_fact`/`remove_fact`, holding the RLock.
  At 10k facts/category that is a 10k-vector numpy bundle per write. HRR superposition also has
  a hard SNR cliff (`hrr.snr_estimate`) around `dim/4` (~256 facts at `hrr_dim=1024`), past
  which the bank vector is noise. The bank is unused by `search_facts` (which is FTS5-only), so
  this is pure tax on the very lock recall contends for.
- **C-3 `memory_versioning.py` is a markdown-FILE snapshot log.** It is content-addressed
  snapshots keyed by `file_name` + an append-only `index.jsonl` (`record_version(file_name,...)`,
  `agent/memory_versioning.py:47-90`). It has NO per-fact granularity, NO `t_valid`/`t_invalid`
  columns, NO `invalidate()` for a SQLite row. Every candidate that claimed to "reuse it for
  bi-temporal facts" was asserting an API that does not exist. Bi-temporal invalidation must be
  BUILT NEW.
- **C-4 the delegate child SHARES the parent's session DB, and session_search is DB-wide and
  unfenced.** `delegate_tool.py:1347` passes `session_db=getattr(parent_agent,"_session_db",None)`;
  `skip_memory=True` (`delegate_tool.py:1344`) only disables the external provider + local
  store, NOT the SessionDB. `hermes_state.py:search_messages` (3383-3491) has NO `session_id`
  WHERE clause (only `active`/`source`/`role` filters), and `tools/session_search_tool.py` has
  ZERO `scan_for_threats` (verified: grep count 0). So a delegate child's messages land in the
  orchestrator's `state.db` and ARE returned, unscanned, by the parent's `session_search`. The
  real cross-agent leak is this channel, not the `on_delegation` provider hook (which is a
  no-op end to end: base `on_delegation` at `memory_provider.py:231`, neither Honcho nor
  holographic override it).

One more verified constraint that shapes every read decision:

- **C-5 the injection fence is whole-block, blanking everything on one hit.**
  `build_memory_context_block` (`agent/memory_manager.py:296-323`) runs
  `scan_for_threats(clean, scope="strict")` over the ENTIRE concatenated string and on ANY hit
  replaces the WHOLE body with `[BLOCKED ...]`. Fusing four planes into one string and sending
  it through this fence creates a cross-store availability DoS: one poisoned row blanks the good
  user-authored facts too. The design must scan and fence PER PLANE.

---

## Decision A - Combine-on-read (the retrieval-and-merge layer)

### A. Winner: A1-parallel-rrf (parallel fan-out + weighted RRF + source-tier prior + per-source floors), HARDENED

We adopt the parallel-query-then-RRF-fuse architecture (A1) as the spine, because it is the
only candidate that actually builds the missing layer the mission requires (Phase 1 central
finding: there is no retrieve-and-merge on the agent recall path) AND matches the Phase 2 brief
verbatim (parallel fan-out, RRF k=60, weighted RRF + source-tier prior + per-source floors,
optional rerank default-OFF). A1 came back `wounded`, not `killed`: its mechanism is right; its
flaws were all in treating the holographic store as a free read, putting the whole thing on the
synchronous hot path always, and routing the fused string through the whole-block fence. Those
are fixable and are folded in below.

The MergeLayer lives at the edge as a new module `agent/memory_merge.py` plus per-store
adapters; the only core touch is replacing the concat step inside `prefetch_all`
(`agent/memory_manager.py:473-493`) with a call into the merge layer. That is the same seam the
Phase 2 brief named (`MemoryManager.build`'s concat step), so it is a surgical core edit, not
new core surface.

### A. Rejected alternatives and exactly why

- **A2-router-first (gate planes by a cheap intent classifier first).** Rejected. The Phase 2
  brief explicitly names routing-first as the road NOT taken: "a wrong route is an unobservable
  recall miss." The red-team confirmed the canonical failure with A2's own example rules (an
  "exact-fact"-shaped query whose answer lives only in Honcho is confidently routed away, and
  being high-confidence it suppresses the fallback). A2 also leaned on a non-existent
  `resolve_runtime_provider` seam and a "free synchronous holographic backbone" that writes on
  read. We keep a router ONLY as the cheap gate on the two EXPENSIVE remote planes (Honcho /
  GBrain), always with a parallel fallback, never as the primary combiner. That hybrid
  refinement is folded into the winner, not adopted as the architecture.

- **A3-synthesis-broker (an aux-LLM rewrites all candidates into one cited brief).** Rejected.
  For a MEMORY system whose entire value is fidelity, a lossy LLM paraphrase on the read path is
  a fidelity regression: the grounding guard only checks that `[store#id]` citations resolve, not
  that the sentence faithfully represents the fact (negation flips, dropped qualifiers, a path
  segment lost all pass). It also puts a blocking aux-LLM round-trip on the synchronous turn path
  on every cold cache, and its async-fan-out-from-a-sync-method bridge is unbuilt as specified.
  We DO keep A3's one good idea as an opt-in upgrade: a synthesis path is allowed only on the
  cost-tiered synthesis surface (where a downstream LLM hides the latency), default-OFF, and only
  over the prose/identity tier, never over exact/verbatim facts.

- **A4-tools-only (recall_mode:tools, delete auto-inject, model elects to recall).** Rejected as
  the architecture. Its headline "single chokepoint" claim is FALSE on the real tree:
  `session_search` stays registered and unfenced (grep: 0 `scan_for_threats`), so a model call to
  it bypasses the meta tool entirely. Flipping to pull-based also regresses turn-1 identity
  continuity (today the provider push-injects), and its "15-token nudge" mitigation re-introduces
  a per-turn synchronous pre-check that is blind to the remote planes where the miss occurs. A
  tool-addressable `memory_search` meta tool is still WORTH HAVING as a complement (model-elected
  deep recall), but it is additive, not the always-on path.

### A. The hardened winner (mitigations folded in)

The MergeLayer is `parallel fan-out -> per-plane sanitize+scan -> weighted RRF -> source-tier
prior -> per-source floors -> abstention floor -> optional rerank -> per-plane-fenced render`,
with a single hard wall-clock budget and a recall trace. Concretely:

1. **Holographic is a TRUE read on the recall path (fixes A1 fatal #1 / C-1).** Add a
   `search_facts_readonly()` variant to `store.py` that runs the same FTS5 SELECT but does NOT
   issue the `retrieval_count` UPDATE+commit and does NOT take a write transaction. The recall
   path calls only this. The `retrieval_count` increment moves to a batched background writer
   (a single deferred `UPDATE ... IN (ids)` drained on the existing `mem-sync` worker), so trust
   signals are still collected without a commit-under-RLock on every recall. Additionally the
   read pool gets its OWN read-only WAL connection (`mode=ro`) separate from the single write
   connection, so concurrent recalls do not serialize behind the background writer or
   `_rebuild_bank`. (WAL allows concurrent readers; the write connection stays single.)

2. **Cheap router gates ONLY the expensive remote planes (folds in A2's one good idea).** Local
   session FTS5 + holographic FTS5 are the always-on synchronous backbone. A rules-first cheap
   router (regex/keyword classes: identity, temporal, entity, exact) decides whether to ALSO
   fire Honcho and/or GBrain, but ASYMMETRICALLY: for any intent whose answer can live in
   exactly one remote plane (identity -> Honcho, entity -> GBrain), that plane is ALWAYS fired
   regardless of router confidence (this is the per-source floor applied at the routing layer).
   A misroute can therefore never zero out a sole-source plane. The router only suppresses a
   plane that is redundant, never one that is uniquely authoritative.

3. **A single hard wall-clock budget on the whole merge call (fixes A1 fatal #2).** The merge
   runs on the synchronous turn-build path at `turn_context.py:374` ONLY for the local backbone
   (sub-ms, no remote). Remote planes are fired with `asyncio.wait_for` per-leg deadlines
   (measured p99, see open question O-3) AND the whole call is bounded by one wall-clock budget
   measured against `turn_context.py:374`. On deadline, fuse whatever returned; RRF degrades
   gracefully because a missing store contributes 0. We do NOT double-wrap Honcho's existing
   internal 8s timeout + background thread (`honcho/__init__.py`); we consume its bounded
   contract directly and give GBrain a real `asyncio` cancellation path (reusing the aggregator's
   async httpx client, not raw threads spinning per-call event loops).

4. **Per-plane sanitize + scan, drop only the offending plane (fixes A1 fatal #3 / C-5).** Each
   plane's candidate text is run through `sanitize_context` + `scan_for_threats(scope="strict")`
   INDIVIDUALLY in its adapter. A plane that hits a threat pattern is DROPPED (its candidates
   excluded from fusion) and recorded in the trace as `blocked`. The fused, ranked block is then
   rendered and the existing `build_memory_context_block` whole-string fence still runs as a
   belt-and-suspenders final gate, but because each plane was already cleaned, one poisoned
   remote row can no longer blank the user's good local facts. This closes the cross-store DoS
   the red-team found A1 created.

5. **NL -> keyword expansion before every FTS5 MATCH (fixes A1 quality risk #1).** Both
   `search_facts_readonly` and `session_search` receive an OR-expanded query (the exact fix
   `memory-stack/recall_probe.py` already proved: 0.62 NL vs 1.00 OR). The expansion is a cheap
   deterministic tokenizer pass (split, drop stopwords, join with OR), folded into the adapter.
   `recall_probe.py`'s cases become part of the CI gate.

6. **Abstention / relevance floor (fixes A1 quality risk #6).** If the top fused score is below
   a frozen threshold, the MergeLayer returns an EMPTY memory block rather than injecting
   low-relevance candidates as "authoritative reference data." Injecting noise labelled
   authoritative is worse than nothing.

7. **Semantic dedup before slot allocation (fixes A1 quality risk #2 / consistency #2).** Before
   RRF allocates the final 8 slots, candidates are collapsed by the holographic HRR vector
   cosine (the vectors are already computed and otherwise unused by `search_facts`) plus a
   normalized-text-hash fallback. Paraphrases of the same fact collapse to one slot and do not
   get double-counted as independent cross-store consensus.

8. **Static priors/weights ship dark until gold-set-calibrated.** Default plane weights all 1.0,
   source-tier prior {user-authored local 1.0, curated 0.85, bulk auto-captured 0.5, stale/archive
   0.5x demote-never-filter}, k=60. These are the Phase 2 defaults, but the WHOLE feature ships
   behind `memory.merge.enabled: false` until the req-#7 eval lands and cross-store fused
   recall@5 clears the frozen floor. We do not trust tuned-looking constants on guessed p99s.

9. **Retire the dead HRR rebuild from the read pool's contention (fixes A1 fatal #4).** Confirm
   the bank is unused by the recall path (it is: `search_facts` is FTS5-only), make
   `_rebuild_bank` incremental (bundle is associative: add/subtract a single vector, no
   full-category re-read) and move it to the batched background writer, and cap category size at
   the SNR-valid bound (~`dim/4`) by sharding categories above it. This is a holographic
   write-path change carried by Decision C; the MergeLayer just stops contending with it.

The MergeLayer emits the req-#4 recall trace on every call: `{query, expanded_query, planes
queried, planes blocked, planes timed-out, per-plane hits with native_rank + native_score,
fused ranking, source-tier multipliers applied, final slots, per-plane latency_ms, total
latency_ms, abstained: bool}`.

---

## Decision B - Subagent <-> orchestrator (isolation + promotion)

### B. Winner: B2-gated-promotion (extract-and-reconcile a distilled summary into one explicit namespace), HARDENED and PRECONDITIONED on closing the real leak

We adopt gated, opt-in, single-namespace promotion (B2) because it is the only candidate that
delivers the req-#10 contract the mission demands: a defined, tested path for what promotes from
a sub-agent to the orchestrator, plus a test that proves nothing else does. It matches the
Phase 2 brief (promote only the condensed `entry['summary']`, through the injection scan,
extract atomic facts, dedup with recency-wins-and-invalidate, into a single `orchestrator/shared`
namespace the orchestrator alone prefetches; default OFF). B2 came back `wounded` for fabricated
seams (memory_versioning bi-temporal, namespace-as-schema-column) and for running on the
synchronous return path; those are folded in.

CRITICAL precondition: B2 (and every promotion design) is MOOT until the REAL leak is closed.
The B1 red-team proved, and we re-verified, that the actual child->parent channel is NOT the
provider hook but the SHARED session DB (C-4): the delegate child writes into the parent's
`state.db` and the parent's DB-wide unfenced `session_search` returns it. So the winner ships in
two parts.

### B. Rejected alternatives and exactly why

- **B1-isolated-only (gate the `on_delegation` hook, promote nothing).** KILLED by its own
  evidence and ours. Its three guards (gate `on_delegation`, early-return in the manager, assert
  base no-op) change ZERO runtime behavior, because `on_delegation` is already a no-op end to end
  (no provider overrides it). Meanwhile its own req-#10 assertion ("a child sentinel never
  appears in the orchestrator's session FTS5") is RED on first run, because the child shares the
  parent's `state.db` and FTS is DB-wide. B1 seals a shut door and ignores the open window. We
  ADOPT B1's correct instinct (isolated-by-default) but it is not a design on its own; it
  contributes nothing to reqs #4/#7/#9 and leaves the real leak open.

- **B3-read-through (the child reads the orchestrator's memory at spawn).** Rejected as the
  promotion answer (it is the inverse direction). Its access model is self-contradictory
  ("borrow the live handle" AND "open a second `mode=ro/immutable=1` connection" are mutually
  exclusive on a single WAL connection), and `immutable=1` on a live-written WAL DB is
  documented corruption territory. We DO keep B3's one safe idea, re-engineered: when a child
  needs orchestrator context, the PARENT runs the recall on its own connection at spawn time and
  passes the resulting frozen, sanitized candidate list down as inert text (the same model as the
  markdown snapshot). No child ever touches `memory_store.db` or a provider object. This is an
  optional spawn-time read-down, default-OFF, separate from promotion.

### B. The hardened winner (mitigations folded in)

Part 1 - close the real leak (this is the load-bearing fix, and it is a justified core touch):

- **Scope `session_search` to the session lineage by default.** Add a `session_id` / lineage
  filter to `hermes_state.py:search_messages` (3450+ `where_clauses`) and to the
  `session_search` discovery path, defaulting to the active session's lineage root (the
  `_resolve_to_parent` chain already exists at `session_search_tool.py:68`). A delegate child's
  messages, written under the child's own `session_id` into the shared `state.db`, are then NOT
  returned by the parent's default `session_search`. Cross-session search remains available
  explicitly (a `scope=all` argument) but is no longer the default.
  IMPLEMENTATION CORRECTION (post-build, adversarial-review P2): the delegate-child fence is
  TWO-LAYERED, not lineage alone. (1) The lineage walk itself excludes `subagent`/`tool`-source
  descendants and their whole subtree (`_lineage_session_ids` filters on `sessions.source`), so
  the child is out of scope at the scope level. (2) The post-query `exclude_sources` filter on
  `_HIDDEN_SESSION_SOURCES = ("subagent","tool")` is the second layer. A legitimate non-subagent
  branch descendant of the same conversation stays in scope. Either layer alone closes the live
  delegate path (children are created `source="subagent"`); both together mean a future change to
  one layer cannot silently re-open the leak. Proven in
  `tests/tools/test_session_search_isolation.py::test_lineage_helper_excludes_subagent_descendant_at_scope_level`.
- **Run `session_search` output through the threat fence (fixes req #11 gap / A4 finding).**
  `tools/session_search_tool.py` currently returns unscanned FTS5 content. Route its results
  through `scan_for_threats` (per-row) before return, so the recall chokepoint claim becomes
  true across ALL channels.

Part 2 - the promotion contract (B2 hardened):

1. **Promotion is OFF by default**, gated by `memory.promote_subagent_results: {off | explicit
   | summarize | auto}`. Only `off` and one implemented mode (`summarize`) ship; `explicit` and
   `auto` are config-validation ERRORS until implemented (no silently-no-op enum values, which
   the B1 red-team correctly called a footgun).

2. **Promotion runs in the BACKGROUND, not on the synchronous return path (fixes B2 race #1).**
   The synchronous `on_delegation` call at `delegate_tool.py:2638` only ENQUEUES the raw
   `entry['summary']` (`<=500` chars, `delegate_tool.py:2070`) onto the existing dreaming /
   background queue. The expensive part (injection scan + capable-model extraction + dedup +
   write) runs on the dreaming plugin's background pass, off the user-visible turn. No multi-agent
   turn eats an LLM call or an O(n) bank rebuild.

3. **`orchestrator/shared` is a real SCHEMA column, not a tag convention (fixes B2 fatal #2).**
   Add a `source_store` (namespace) column to the holographic `facts` schema with an index, and
   make `search_facts_readonly` namespace-filtered. Promotion writes ONLY to
   `namespace='orchestrator/shared'`; the orchestrator's prefetch reads ONLY that namespace plus
   its own. This is what makes "nothing else promotes / nothing else surfaces" provable at the
   store level rather than by string convention. (This column is also what Decision C uses for
   one-plane-per-fact routing, so it is built once.)

4. **The promoted summary is wrapped `untrusted_tool_result` AND scanned as it enters live
   context (fixes B1 fatal #4).** Today the delegate summary is a plain `summary[:500]` string
   returned unwrapped (verified: grep for `untrusted` in `delegate_tool.py` returns nothing).
   The summary the parent SEES in live context is wrapped and scanned; the summary that is
   PROMOTED into the store is scanned + redacted on ingest (per the MEMORY-POLICY redaction
   pass, Decision C).

5. **Dedup is semantic, supersede is real bi-temporal (fixes B2 fatal #1 / #3).** Promotion
   reconciliation uses the new bi-temporal fact substrate from Decision C (NOT
   `memory_versioning.py`, which cannot version a row). Recency-wins MARKS the superseded fact
   invalid (`t_invalid` set), never overwrites or deletes. Semantic near-dup detection (HRR
   cosine) collapses paraphrased re-promotions so repeated delegations converge instead of
   accumulating contradictory near-duplicates.

6. **Heavy outputs promote an artifact REFERENCE, not content**, with a referential-integrity
   TTL/existence check at read so a ref cannot dangle past child/artifact GC (v2 already has the
   artifact registry + `artifact.created` SSE).

The req-#10 proof test (Phase 5) asserts, on a REAL `delegate_task` (not a persona): (a) with
promotion `off`, a child sentinel is NOT returned by the parent's default `session_search` and
NOT present in any holographic namespace; (b) with promotion `summarize`, ONLY the distilled
summary appears, ONLY in `orchestrator/shared`, and the raw child transcript does not; (c) the
cross-profile channels (`_resolve_profile_db`, `_locate_session_db` scanning every profile) are
gated under `subagent_isolation_strict`. The test quiesces the background workers before any
byte/row diff (the diff is otherwise non-deterministic because `sync_all` / dreaming /
`queue_prefetch_all` mutate the stores independently).

---

## Decision C - Write path (extract-and-reconcile, one plane per fact)

### C. Winner: C1-extract-reconcile (background extract -> retrieve-similar -> ADD/UPDATE/DELETE/NOOP, one plane per fact), HARDENED with a REAL bi-temporal substrate and HONEST scope

We adopt extract-and-reconcile (C1) because it is the Phase 2 write contract verbatim (Mem0
two-phase, capable write model, one-plane-per-fact, dedup + recency-wins, run in the background
dreaming pass) and it is the only write candidate whose mechanism directly prevents the "same
fact in two stores -> drift" failure Phase 1 flagged. C1 came back `wounded` for the three
fabricated-seam classes (C-1/C-2/C-3) and for a single-provider contradiction; those are folded
in below, and the honest cost is restated.

### C. Rejected alternatives and exactly why

- **C2-append-then-compact (append every turn cheaply, compact on a cold clock).** KILLED. Its
  "already-built, ~150 LOC of wiring" premise is false: the dreaming pipeline reads `state.db`
  and writes markdown ONLY; it has zero code touching the holographic `facts` table, so
  `compact_holographic()` is a net-new pipeline. Its "cheap hot append" claim is contradicted by
  C-2 (every novel write pays full HRR + O(n) bank rebuild synchronously). Its supersede /
  invalidate-don't-delete has no implementation (no `t_valid` columns, `remove_fact` is a hard
  DELETE), and its review queue cannot represent holographic fact ops. It also reuses a config
  key (`memory.write_mode`) that the v28->v29 migration renamed, a live footgun. We DO keep its
  one correct instinct (a cheap synchronous write for read-your-writes) as the FTS5-immediate
  write below.

- **C3-markdown-canonical (markdown is the system of record, all stores are rebuildable
  projections).** Rejected. It explicitly does NOT touch recall ("the point of C3 is NOT to
  change read"), so it delivers nothing on req #7, the metric that defines success. Its
  load-bearing `save_to_disk` "post-write hook" does not exist; the existing `_detect_external_drift`
  guard would actively REFUSE the reconciler's own writes; and the 2200-char `memory_char_limit`
  makes "one unbounded file per category" structurally impossible at 10k facts. We DO keep its
  disaster-recovery instinct as a bounded promise (see req #2 / the rebuild guarantee), not as
  the architecture.

### C. The hardened winner (mitigations folded in)

The write path is the existing dreaming background pass, upgraded to a reconcile engine, with
ONE cheap synchronous exception for read-your-writes:

1. **A REAL bi-temporal fact substrate (fixes C1 fatal #1 / #2, C-3).** Do NOT extend
   `memory_versioning.py` (it is a markdown-file snapshot log and cannot version a row). Instead
   ALTER the holographic `facts` schema to add `t_valid`, `t_invalid` (nullable), `supersedes_id`,
   and the `source_store` namespace column (shared with Decision B). `search_facts_readonly`
   filters `t_invalid IS NULL` by default; an `as_of=<ts>` argument enables "as of" reasoning.
   `invalidate()` SETS `t_invalid`, never deletes. `remove_fact`'s hard DELETE is reserved for
   redaction/GC only and is gated behind the backup precondition (req #2). This is a holographic
   store schema change; we own it explicitly (it is NOT "store.py used as-is").

2. **Resolve the Honcho-vs-holographic provider contradiction honestly (fixes C1 fatal #3).**
   The single-external-provider lock (`add_provider`, `memory_manager.py:367-371`) stays.
   Holographic is NOT registered as a second provider. The reconcile engine writes holographic
   OUT-OF-BAND (direct store handle, off the provider path), while Honcho remains the sole
   registered provider and keeps ingesting raw turns via `sync_turn`. One-plane-per-fact routing
   is then: identity/representation -> Honcho (the registered provider, raw turns only, let the
   Deriver synthesize); entities + relations -> GBrain (when up; otherwise queued, NEVER dumped
   into the FTS5 plane); exact/verbatim facts (paths, keys, IDs, decisions) -> holographic FTS5
   (written out-of-band by reconcile). A Phase-5 test asserts Honcho's `sync_turn` still fires
   under this arrangement.

3. **Two-phase `add_fact` so the hot write is cheap (fixes C1 scale, folds in C2's instinct).**
   Split `store.py:add_fact` into a hot INSERT (content + `source_store` + `tags`, NO entity
   extraction, NO HRR encode, NO bank rebuild) and a cold enrichment (`defer_enrichment=True`).
   The reconcile engine and the read-your-writes path use the hot INSERT; the dreaming pass does
   the enrichment in the background. `_rebuild_bank` becomes incremental (associative bundle
   add/subtract, no full re-read) and batched per cycle, and categories are capped/sharded at the
   SNR-valid bound (~`dim/4`).

4. **Read-your-writes via an immediate FTS5 write (fixes C1 consistency #1).** A fact the model
   states this turn is written to the holographic FTS5 row synchronously via the cheap hot INSERT
   (cheap: one INSERT, no HRR/bank work), so the same/next turn can recall it. The expensive
   enrichment + the Honcho representation derive in the background. The curated/reconciled view
   lags by one dreaming cycle, but the fact is recallable immediately.

5. **Concurrency control on the reconcile writes (fixes C1 races).** Each candidate's
   retrieve-similar -> emit-op -> apply runs in one transaction with an optimistic version check
   (compare-and-set on `updated_at`). Facts carry a stable external key (a content-hash UUID, not
   the recycled AUTOINCREMENT `fact_id`) so UPDATE/DELETE/invalidate cannot target a recycled id.
   The reconcile op stream is a durable, idempotent, write-ahead op-queue keyed by op-id, so a
   partial/failed dream cycle is resumable (req #5). The dreaming `_run_lock` non-blocking-skip is
   replaced with a coalescing queue so triggers are not silently dropped.

6. **NL -> keyword expansion on the retrieve-similar step (fixes C1 quality risk #1).** The
   ADD-vs-UPDATE decision depends on retrieve-similar actually finding the near-dup; the OR-expansion
   from Decision A is applied here too, plus HRR-cosine semantic retrieve-similar, so paraphrased
   facts are matched and the store does not rot into the near-dups C1 exists to prevent.

7. **Redaction on ingest (req #8).** Every candidate is run through the MEMORY-POLICY redaction
   regex (secrets / API keys / credentials / sensitive PII) BEFORE the INSERT, and the content
   shown to the cold reconcile LLM is wrapped untrusted. Storing untrusted spans then
   LLM-processing them is a new attack surface; this fence closes it.

8. **Honest scope.** C1 governs BACKGROUND reconcile writes and the read-your-writes hot write.
   The model-invoked `memory` / `fact_store` tools are ALSO routed through the same one-plane-per-fact
   + redaction contract (so the inline path cannot re-open the drift). Where a write must be
   destructive (redaction, GC), it stops at the req-#2/#3 backup-and-pause gate.

---

## 4. The unified end-to-end design

One coherent picture. Edge-vs-core is flagged on every piece; every core touch is justified.

### Read path (per turn)

```
turn build (turn_context.py:374, synchronous):
  query --> NL->keyword OR-expansion
        --> MergeLayer.recall(query):
              backbone (always, sync, sub-ms):
                 - session FTS5  (search_messages, lineage-scoped, OR-expanded)   [adapter]
                 - holographic FTS5 (search_facts_readonly, NO write, ro WAL conn) [adapter]
              remote (router-gated, asyncio.wait_for per-leg + 1 wall-clock budget):
                 - Honcho peer.context()   (fired always for identity intent)      [adapter]
                 - GBrain entities/relations (fired always for entity intent)      [adapter]
              per plane: sanitize_context + scan_for_threats(strict); drop+trace if hit
              --> semantic dedup (HRR cosine + text-hash)
              --> weighted RRF (k=60)
              --> source-tier prior (user 1.0 / curated 0.85 / bulk 0.5; stale 0.5x)
              --> per-source floors (sole-source identity/entity never buried)
              --> abstention floor (empty block if top score < threshold)
              --> [optional] cross-encoder rerank on top ~20-30 (default OFF, cost-tier)
              --> render top-8, each plane already fenced
        --> build_memory_context_block (final whole-block fence, belt-and-suspenders)
  emit RecallTrace {query, expanded, planes queried/blocked/timed-out,
                    per-plane hits+scores, fused ranking, latencies, abstained}

model-elected (additive, complements the always-on backbone):
  - memory_search meta tool (deep recall, same MergeLayer, fenced)
  - session_search (now lineage-scoped + fenced)
  - mcp_gbrain_* (model-invoked, fenced on return)
```

Seams touched on read:
- `agent/memory_merge.py` (NEW, edge) - the MergeLayer + adapters + RRF + priors + floors +
  abstention + trace.
- `agent/memory_manager.py:473-493` `prefetch_all` (CORE touch, justified) - replace the
  concat step with a `MergeLayer.recall()` call. This is the exact seam Phase 2 named; it is a
  one-function swap, not new core surface.
- `plugins/memory/holographic/store.py` (CORE-adjacent, plugin) - add `search_facts_readonly`
  (no write) + a read-only WAL connection.
- `hermes_state.py:search_messages` + `tools/session_search_tool.py` (CORE touch, justified) -
  lineage scoping by default + threat scan on return. Required to close the cross-agent leak and
  the req-#11 channel gap; cannot be done at the edge because the leak IS the core query.

### Write path (per completed turn)

```
turn completes:
  - SessionDB.append_message  (unchanged; FTS5 trigger-synced)
  - read-your-writes: if the model stated a durable fact, hot-INSERT it into holographic FTS5
        (cheap: content + source_store + tags, NO HRR/bank) so it is recallable next turn
  - on_delegation (delegate_tool.py:2638): ENQUEUE entry['summary'] (<=500ch) to background
        (promotion only; default OFF)

background dreaming pass (reconcile engine, capable model, off the hot path):
  - extract salient candidates from (rolling summary + window + new turns)
  - redact on ingest (MEMORY-POLICY regex), wrap untrusted
  - per candidate: retrieve-similar (OR-expanded + HRR cosine)
        --> ADD / UPDATE / DELETE / NOOP
  - route ONE plane per fact by type:
        identity/representation --> Honcho (registered provider, raw turns, Deriver synthesizes)
        entities + relations    --> GBrain (queued if down; never dumped into FTS5)
        exact/verbatim facts     --> holographic FTS5 (out-of-band store handle)
  - recency-wins: invalidate() superseded (set t_invalid; never delete)
  - enrich deferred facts (HRR encode + incremental bank update, batched)
  - durable idempotent op-queue keyed by op-id (resumable); optimistic CAS per op
```

Seams touched on write:
- the dreaming plugin (`plugins/dreaming/`, edge) - upgraded with the reconcile engine
  (`reconcile.py`), new input adapter from the facts table, new output writer to the facts
  table. This is net-new pipeline work, not "150 LOC of wiring" (honest scope).
- `plugins/memory/holographic/store.py` (plugin) - bi-temporal columns
  (`t_valid`/`t_invalid`/`supersedes_id`/`source_store`), `invalidate()`, two-phase `add_fact`
  (`defer_enrichment`), incremental `_rebuild_bank`, stable external key, optimistic CAS. A
  schema migration (req #2 backup-gated).
- `agent/memory_manager.py` (CORE-adjacent) - one-plane-per-fact routing in `sync_all` /
  `on_memory_write`; out-of-band holographic write seam.

### Promotion (subagent -> orchestrator)

Default OFF. When `summarize`: background extract of the distilled `entry['summary']` ->
redact + scan -> atomic facts -> semantic dedup + recency-wins-invalidate -> write to
`namespace='orchestrator/shared'` (schema column, not tag). Orchestrator alone prefetches that
namespace. Isolation otherwise airtight: the lineage-scoped `session_search` + the
`subagent_isolation_strict` gate on cross-profile reads are the proof surface.

### Memory Supervisor (req #12a, runtime)

A resilient supervisor (`gateway/memory_monitor.py` + `gateway/platforms/memory_aggregator.py`,
edge) over every store and background job: detects store outages (the Phase 1 silent-degradation
finding), circuit-breaks and fails over (fail-OPEN for recall, fail-CLOSED for writes), retries
stuck jobs with backoff, gates writes behind backup + injection, triggers compaction + the eval,
and exposes a health surface. The MergeLayer's per-plane `timed-out`/`blocked`/`down` trace
signals feed it, so a down Honcho/GBrain is now visible instead of silently empty.

### Config knobs + defaults

```yaml
memory:
  merge:
    enabled: false            # ships dark until req-#7 eval clears the floor
    rrf_k: 60
    plane_weights: { local: 1.0, holographic: 1.0, honcho: 1.0, gbrain: 1.0 }
    source_tier_prior: { user_authored: 1.0, curated: 0.85, bulk: 0.5, stale_multiplier: 0.5 }
    per_source_floors: true   # sole-source identity/entity never buried
    abstention_floor: <calibrated from gold set>
    nl_keyword_expansion: true
    remote_router: rules      # gates ONLY Honcho/GBrain; asymmetric (sole-source always fires)
    timeout_ms: { honcho: <measured p99>, gbrain: <measured p99> }   # see O-3
    wall_clock_budget_ms: <measured>
    rerank: { enabled: false, provider: bge-local }                  # cost-tier, synthesis path only
    synthesis: { enabled: false }                                    # A3's idea, opt-in, prose tier only
  write:
    reconcile: { enabled: false, model: <capable>, cadence: on_session_end }
    read_your_writes_fts: true
  promote_subagent_results: off    # off | summarize  (explicit/auto = config error until built)
  subagent_isolation_strict: true  # gates cross-profile session reads
  supervisor: { enabled: true, fail_open_recall: true, fail_closed_writes: true }
```

### Edge-vs-core summary

| Piece | Edge or Core | Justification |
|-------|--------------|---------------|
| `agent/memory_merge.py` MergeLayer + adapters | Edge (new module) | Capability at the edges; stores stay decoupled |
| `prefetch_all` concat -> MergeLayer call | **Core touch** | The Phase-2-named seam; one-function swap, no new always-loaded tool |
| `search_facts_readonly` + ro WAL conn | Plugin | Holographic is a plugin; read-only variant is additive |
| Holographic bi-temporal schema + two-phase add + incremental bank | Plugin (schema migration) | Plugin-owned store; backup-gated migration |
| `search_messages` lineage scoping + `session_search` scan | **Core touch** | The leak IS the core query; cannot be fixed at the edge |
| Reconcile engine in dreaming | Edge (plugin) | Background formation off the hot path |
| One-plane-per-fact routing | Core-adjacent | Lives in `memory_manager` sync; no new tool |
| Promotion (background, namespace column) | Edge + plugin schema | Through the existing `on_delegation` seam |
| Memory Supervisor | Edge | Builds on existing monitor + aggregator |
| `memory_search` meta tool | Edge (gated tool) | Additive, not always-loaded |

Total core touches: TWO (the `prefetch_all` swap, and the `search_messages`/`session_search`
scoping+scan). Both are justified: the first is the exact missing-layer seam; the second is the
only place the cross-agent leak can be closed. Everything else is edge or plugin.

---

## 5. Requirements integration

| Req | Where satisfied in THIS design |
|-----|--------------------------------|
| **1 No fabricated evidence** | Standing. Every code claim here re-verified at file:line (store.py:228-238 read-write; delegate_tool.py:1347 shared db; memory_versioning.py:47-90 markdown-only; add_provider:367-371; search_messages:3450 no session filter; session_search_tool scan-count 0; build_memory_context_block:296-323 whole-block fence). Phase 5 eval prints real recall@k. |
| **2 Backup before any data change** | The holographic schema migration (bi-temporal columns), any `remove_fact`/redaction, and the first live write are all gated behind a timestamped backup to `~/.hermes/backups/` + a documented rollback. Enforced by the Memory Supervisor's write-gate (fail-closed). |
| **3 Hard stop before irreversible actions** | `invalidate()` (set `t_invalid`) replaces DELETE everywhere; the only hard DELETE (redaction/GC) pauses for explicit user go. Reversible work proceeds. |
| **4 Observability** | The MergeLayer emits a per-recall RecallTrace (query, expanded query, planes queried/blocked/timed-out, per-plane hits + native scores, fused ranking, source-tier multipliers, final slots, per-plane + total latency, abstained). Per-store attribution is reliable because the read pool no longer shares the write lock (ro WAL connection). Write side logs op-id + plane + op-type. |
| **7 Retrieval quality is the metric** | `tests/tools/test_memory_recall_eval.py` (or `skills/memory-eval/`) drives `MemoryStore` directly. Frozen YAML gold set (~30-50 facts + ~20 queries) spanning single-hop / multi-hop / temporal / knowledge-update / abstention, each with BEIR-style `relevant_fact_ids`. Computes Recall@{1,3,5,10}, Precision@k, Hit-Rate@k, MRR, nDCG@10, AND cross-store fused recall@5 (not just FTS5). `recall_probe.py` NL-vs-OR cases folded in. RULER-style scale-stress (1 fact among 10/100/1000 distractors). CI gate: fail if fused recall@5 or MRR drops below the frozen floor; `merge.enabled` stays false until green. Nightly LLM-judge (different judge model, >=5 runs, stratified by question type). |
| **8 Schema + store/not-store + redaction** | `docs/memory-audit/MEMORY-POLICY.md` (Phase 3 deliverable) defines the fact schema (content, category, tags, `source_store`, `t_valid`/`t_invalid`/`supersedes_id`, trust, stable external key) and the store/not-store policy. Redaction regex (secrets / API keys / credentials / sensitive PII) runs on ingest BEFORE every INSERT, enforced in the reconcile engine and the inline `memory`/`fact_store` tools. |
| **9 Retention and compaction** | The reconcile engine is the compaction (dedup + recency-wins-invalidate). The existing curator / `.archive/` sweep is connected to the holographic facts table: low-trust, zero-retrieval, aged, invalidated facts are archived (not the markdown curator path applied to rows; a real facts-table sweep). Category sharding at the SNR bound keeps banks bounded. Growth is no longer unbounded. |
| **10 Isolation + promotion explicit and tested** | Isolation: lineage-scoped `session_search` + `subagent_isolation_strict` gate. Promotion: default OFF; `summarize` writes ONLY the distilled summary, ONLY to `orchestrator/shared` (schema column). The req-#10 test runs a REAL `delegate_task`, quiesces background workers, and asserts (off) no child sentinel anywhere, (summarize) only the distilled summary in only that namespace, and cross-profile reads gated. |
| **11 Prompt injection hardening** | Per-plane `sanitize_context` + `scan_for_threats(strict)` in EVERY adapter (drop+trace the offending plane, not blank-everything). `session_search` now scanned on return (was the verified gap). Promoted summaries wrapped `untrusted_tool_result` + scanned on ingest. The whole-block `build_memory_context_block` fence stays as a final belt-and-suspenders. Phase 5 test stores injection payloads then recalls through every channel and asserts they are caught. |
| **12a Memory Supervisor (runtime)** | Resilient supervisor over stores + jobs (fail-open recall, fail-closed writes, circuit-break, backoff retries, write-gate, compaction/eval triggers, health surface), fed by the MergeLayer's down/timed-out/blocked trace so outages are visible (closes the Phase 1 silent-degradation finding). |
| **12b Build-execution orchestrator** | The phased build plan (section 6) is the self-healing harness: decompose -> implement -> verify with gates -> on failure diagnose + fix-swarm -> re-verify -> loop, with timeouts and a completeness critic. |

Resumable/idempotent (req #5) and the proof script (req #6) are Phase 5: the eval gold set is
deterministic, writes are upsert-by-stable-key, destructive checks use temp scratch, and
`docs/memory-audit/proof/prove_memory.sh` stores known items and proves retrieval through EACH
mechanism (session FTS5, holographic facts, Honcho, GBrain, per-agent isolation) with real
printed output.

---

## 6. Phased build plan (each step independently testable)

The working slice first: a real combined recall over the two LOCAL planes, behind a passing
eval, with a trace. Remote planes and the write reconcile follow once the gate exists.

1. **Eval harness + gold set FIRST (req #7).** Build `test_memory_recall_eval.py` + the frozen
   YAML gold set + the CI gate, driving `MemoryStore` directly. No merge yet; it measures
   today's FTS5 baseline. Independently testable: `eval.py --threshold ... || exit 1` runs green
   on the baseline. This is the gate everything else must clear.

2. **`search_facts_readonly` + read-only WAL connection + NL->OR expansion (holographic).**
   Fix C-1 at the source: a true read variant, no `retrieval_count` write, separate ro
   connection, OR-expanded query. Testable: the eval's NL cases jump from ~0.62 to ~1.00; a
   concurrency test proves recalls no longer serialize behind the writer.

3. **The MergeLayer over the two LOCAL planes only (the working slice).** `agent/memory_merge.py`
   + session-FTS5 adapter + holographic adapter + weighted RRF + source-tier prior + per-source
   floors + abstention floor + semantic dedup + per-plane scan + RecallTrace. Swap `prefetch_all`'s
   concat for `MergeLayer.recall()`, still behind `merge.enabled: false` but exercised by the eval.
   Testable: cross-store fused recall@5 over {session, holographic} clears the floor; the trace is
   emitted; a poisoned row in one plane drops only that plane (not the whole block).

4. **Close the cross-agent leak (req #10/#11 core touch).** Lineage-scope `search_messages` +
   `session_search`; add the threat scan on `session_search` return; add the
   `subagent_isolation_strict` gate. Testable: the req-#10 negative-space test (real
   `delegate_task`) goes from RED to GREEN; an injection payload stored as a child message is no
   longer returned to the parent.

5. **Bi-temporal holographic schema + two-phase `add_fact` + incremental bank (backup-gated).**
   Add `t_valid`/`t_invalid`/`supersedes_id`/`source_store`, `invalidate()`, `defer_enrichment`,
   incremental associative bank update, stable external key, optimistic CAS. Backup first (req #2).
   Testable: an UPDATE marks-not-deletes (old fact recallable `as_of`); a 10k-fact write no longer
   triggers an O(n) rebuild; the SNR cliff is enforced by category sharding.

6. **The reconcile write engine in the dreaming pass (one-plane-per-fact, redaction, op-queue).**
   Extract -> redact -> retrieve-similar -> ADD/UPDATE/DELETE/NOOP -> route one plane per type ->
   recency-wins-invalidate; durable idempotent op-queue; read-your-writes hot FTS5 write.
   Testable: the eval's write-quality cases (right-fact-stored, dedup, knowledge-update-outranks-old,
   abstention) pass; a killed-mid-cycle reconcile resumes by op-id.

7. **Remote planes into the MergeLayer (router-gated, measured p99 deadlines).** Bring Honcho +
   GBrain up (req: Docker + OpenRouter credits, user-gated), MEASURE p99, set the deadlines + the
   wall-clock budget, wire the asymmetric router (sole-source always fires). Testable: with a
   plane forced-down, the turn still completes within budget and the trace shows `timed-out`/`down`;
   fused recall improves on cross-store queries.

8. **Promotion (default OFF) + the req-#10 promotion test.** Background-enqueue the distilled
   summary; reconcile into `orchestrator/shared`; wrap+scan. Testable: the promotion test (off vs
   summarize) passes; nothing else promotes.

9. **Memory Supervisor + flip `merge.enabled: true` once the gate is green.** Health surface,
   circuit-break, fail-open recall / fail-closed writes, eval trigger. Testable: an injected store
   outage surfaces on the health endpoint and does not cascade-fail the agent.

10. **Optional upgrades (cost-tiered, default OFF): cross-encoder rerank; A3 synthesis path over
    the prose tier only; `memory_search` meta tool.** Each gated, each measured against the eval +
    latency/token budgets before being trusted.

---

## 7. Open questions

### Resolved by the lead (decided here, not punted)

- **Architecture = parallel RRF, not routing-first, not synthesis-first.** Decided (Decision A).
- **Routing is a gate on remote planes only, asymmetric so sole-source planes always fire.**
  Decided; resolves Phase-2 Q1's "what triggers gating" with rules-first + the per-source-floor
  guarantee.
- **Promotion default = OFF for ALL personas, including Atlas/Forge/etc.** Decided; resolves
  Phase-2 Q6. The personas already own isolated memory scopes; auto-promote is the documented
  CrewAI failure. They may opt into `summarize` per profile, but the default is `off`.
- **Bi-temporal is BUILT NEW on the holographic schema, not on `memory_versioning.py`.** Decided
  (Decision C); resolves Phase-2 Q5's "where does validity live." Full bi-temporal for
  exact/verbatim facts; Honcho keeps its own representation behavior; GBrain keeps its own
  contradiction enum (never auto-applied).
- **Reranker default = OFF, local `bge`/FlashRank seam, only on the synthesis path.** Decided;
  resolves Phase-2 Q4.
- **Markdown is NOT the system of record (C3 rejected).** The disaster-recovery promise is
  bounded and honest: markdown + FTS5 facts rebuild faithfully; HRR banks rebuild approximately;
  GBrain rebuilds at cost; Honcho and `state.db` chat history do NOT rebuild. Resolves Phase-2 Q8.

### Genuinely need the USER's decision (architectural forks or cost/destructive choices)

- **O-1 (cost, blocks step 7): bring Honcho + GBrain up.** Measuring the real p99 deadlines and
  validating cross-store fused recall requires Docker + OpenRouter credits (Honcho deriver +
  GBrain engine both route LLM/embeddings via OpenRouter). This is paid + heavy (Phase-1 Q2).
  Until you green-light it, the merge layer ships LOCAL-only (steps 1-6) and the remote planes
  are dark. Decision needed: spin them up now, or ship local-only first?
- **O-2 (destructive, blocks step 5): the holographic schema migration.** Adding bi-temporal
  columns to `~/.hermes/memory_store.db` is a schema change to a store that does not exist yet
  (no `memory_store.db` today, since Honcho is the active provider). If we make holographic the
  out-of-band fact store, we are creating that DB and committing to migrating it later. Confirm
  you want holographic stood up as the durable exact-fact plane (vs leaving it dormant and using
  only session FTS5 for exact facts). This is the one decision that changes how much of Decision
  C we build.
- **O-3 (measurement, blocks the deadline numbers): the real p99s.** We cannot set
  `timeout_ms.honcho/gbrain` or the wall-clock budget until the servers are up and profiled
  (Phase-2 Q3). A guessed-too-tight deadline silently starves the graph plane on every turn; too
  loose reintroduces the turn-stall. We will NOT ship guessed constants; this is gated on O-1.
- **O-4 (compatibility risk): Honcho SDK 2.0.1 vs server 3.0.9 skew** (Phase-1 Q3). If we bring
  Honcho up, the peer-card / conclusions path is an untested version-skew risk. Decision needed:
  pin the server to match the SDK, or upgrade the SDK?
- **O-5 (scope): does the production frontend send `oc_agent_id` for the MAIN chat, or only for
  the `/app/agents` personas** (Phase-1 Q4)? This determines how often the per-agent DB (and thus
  the isolation/promotion machinery) is actually exercised vs the shared DB. If main chat never
  carries an agent id, the promotion path is exercised only by delegate sub-agents, which narrows
  the req-#10 test surface. Please confirm the frontend behavior.
