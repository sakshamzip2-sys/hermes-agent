# Memory Mission — Binding Requirements (session addendum)

These 11 requirements were added mid-session by the user and OVERRIDE any conflicting
instruction in the original brief. Every one is an acceptance criterion: the mission is not
done until each is satisfied with evidence (or listed as an explicit, justified open item).
No em dashes anywhere (house rule).

Status key: [DONE] proven this session, [PLANNED] owned by a later phase, [STANDING] a
discipline applied continuously.

## Engineering discipline

1. **No fabricated evidence.** [STANDING] Every working claim shows the real command and real
   output (see `evidence/EVIDENCE-LOG.md`, E1 to E6). Broken or name-only components are
   stated plainly (Honcho server down, GBrain server down). No trivial tests count as success.

2. **Backup before any data change.** [PLANNED, gates Phase 4] No memory data has been mutated
   yet (all Phase 1 tests used temp DBs / throwaway $HOME / read-only handles). Before the
   first live write to `~/.hermes/state.db`, `~/.hermes/memory_store.db`, `~/.gbrain`, or
   Honcho, take a timestamped backup and document the rollback command. Backups live under
   `~/.hermes/backups/` and `docs/memory-audit/evidence/`.

3. **Hard stop before irreversible actions.** [STANDING] No destructive migration or deletion
   of stored memory without pausing for explicit user go. Reversible work proceeds meanwhile.

4. **Observability required.** [PLANNED, Phase 3 design + Phase 4 build] Every memory read and
   write must be logged with WHICH STORE served each result, so retrieval is debuggable. The
   merge layer must emit a per-recall trace (query, stores queried, per-store hits + scores,
   fused ranking, latency). This is a first-class design output, not an afterthought.

5. **Resumable and idempotent.** [STANDING] The eval harness and proof script must be safe to
   re-run after a session restart (deterministic gold set, upsert-by-key writes, temp scratch
   for destructive checks). Workflows in this session are resumable by runId.

6. **End with a proof script I can run.** [PLANNED, Phase 5 deliverable] Deliver a single
   script that stores known items and proves they can be retrieved through EACH mechanism
   (session FTS5, holographic facts, Honcho, GBrain, per-agent isolation), printing real
   output. Lives at `docs/memory-audit/proof/prove_memory.sh` (+ python helpers).

## What makes or breaks memory

7. **Retrieval quality is the metric, not storage.** [PLANNED, Phase 5] Build a real retrieval
   eval: a fixed set of store-and-query cases with a gold relevance set; report recall@k and
   precision@k numbers per mechanism and for the merged layer. "Stored it and got something
   back" is NOT success.

8. **Schema + store/not-store policy + redaction.** [PLANNED, Phase 3 design] Define the
   memory schema and an explicit policy for what to store vs not. Never store secrets,
   credentials, API keys, or sensitive PII carelessly; redact on the way in. Deliver as
   `docs/memory-audit/MEMORY-POLICY.md` and enforce in the write path.

9. **Retention and compaction.** [PLANNED, Phase 3 design] Memory grows and rots. Define
   summarization / eviction / compaction so it stays useful and bounded. v2 already has a
   curator (config `curator:` block) and the dreaming consolidation; the design must connect
   them to the fact stores, not leave growth unbounded.

10. **Isolation and promotion explicit and tested.** [PARTIAL] Isolation is proven (E4: one
    sub-agent's private memory does not leak to another or to the orchestrator). STILL OWED:
    define EXACTLY what promotes from a sub-agent up to the orchestrator, and a test proving
    nothing else promotes. [PLANNED, Phase 3 design + Phase 5 test]

12. **Resilient orchestration / supervision layer (added mid-session).** [PLANNED, Phase 3
    addendum design + Phase 4 build] There must be an orchestrating layer (one top orchestrator
    plus domain sub-orchestrators) that manages all the sub-agents and background jobs, in TWO
    senses:
    (a) RUNTIME "Memory Supervisor": a resilient supervisor over every store and background
        job. It detects store outages (the Phase 1 silent-degradation finding: Honcho/GBrain
        down today with no signal), circuit-breaks and fails over, retries stuck/failed jobs
        with backoff, gates writes (backup + injection), triggers compaction/retention and the
        eval, and exposes a health surface. Builds on `gateway/memory_monitor.py` and
        `gateway/platforms/memory_aggregator.py`. Must itself be resumable/idempotent and never
        cascade-fail the agent (fail-open for recall, fail-closed for writes).
    (b) BUILD-EXECUTION orchestrator: the self-healing harness that runs Phases 4 to 6 -
        decompose into workstreams, implement, verify with gates, on failure diagnose and
        launch a fix-swarm, re-verify, loop until green; detect stuck/hung agents (timeouts),
        bounded retries, a completeness critic that can launch MORE swarms, no infinite loops.

11. **Prompt injection hardening.** [PLANNED, Phase 3 design + Phase 5 test] Any memory that
    ingests web/scraped/user content can carry attacks. Sanitize on the way in; never let
    stored content be executed as instructions on the way out. v2 already has a threat-scan
    fence in `build_memory_context_block` and `untrusted_tool_result` wrapping; the design
    must verify these cover ALL recall channels (provider prefetch, FTS5, GBrain, fact_store)
    and add coverage where they do not. Test with injection payloads stored then recalled.

## Requirement -> phase ownership map

| Req | Owner phase | Deliverable |
|-----|-------------|-------------|
| 1 No fabrication | all | EVIDENCE-LOG.md |
| 2 Backup | Phase 4 precondition | backups + rollback notes |
| 3 Hard stop | standing | (pause points) |
| 4 Observability | Phase 3 design, Phase 4 build | recall-trace logger |
| 5 Resumable/idempotent | Phase 5 | proof + eval harness |
| 6 Proof script | Phase 5 | proof/prove_memory.sh |
| 7 Retrieval eval | Phase 5 | eval/ + numbers |
| 8 Schema + redaction | Phase 3 | MEMORY-POLICY.md |
| 9 Retention/compaction | Phase 3 | design doc section |
| 10 Isolation + promotion | Phase 3 design, Phase 5 test | promotion contract + test |
| 11 Injection hardening | Phase 3 design, Phase 5 test | hardening section + test |
