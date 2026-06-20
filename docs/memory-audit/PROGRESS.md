# PROGRESS.md — Memory mission persistent state (loop protocol)

The single source of truth for what is done, what failed, what is open. Updated every loop
iteration. Not in CLAUDE.md. No em dashes.

Working dir: `/Users/saksham/Vscode/OpenComputerV2/OC-memory` (git worktree, branch
`feat/memory-mission`, base `feat/agents-orchestrator-mission`). Hermes version pinned: 0.16.0.
Venv: `.venv/bin/python`. Standing rule: commit after every wave (a shared-tree clobber already
ate one uncommitted build; see RECOVERY-NOTE.md). All work isolated in this worktree.

## Definition of done (Part 1 memory subsystem)

Every store proven reachable + correct with real evidence; a real retrieval-and-merge layer on
the agent recall path; per-agent isolation + gated promotion proven; injection-hardened; a
runnable proof script; recall@k / precision@k eval with real numbers; all 12 requirements met.

## DONE and PROVEN (real evidence in evidence/EVIDENCE-LOG.md, tests green)

- Phase 1 recon + per-component verdict (E1 to E9): session FTS5 WORKING; holographic store
  WORKING-but-dormant; Honcho PARTIALLY-WIRED (server down, fails open); GBrain engine WORKING
  (server down); per-agent (gateway) isolation WORKING; delegate-channel leak (C-4) REAL.
- Phase 2 best-practices brief (cited). Phase 3 locked design + orchestration spec.
- Slice 1: retrieval eval harness + frozen gold set (req #7). OR recall@5 = 1.0 vs NL 0.375.
- Slice 1: holographic search_facts_readonly (no write on read, ro WAL conn, NL to OR).
- Wave 2: MergeLayer (parallel fan-out, RRF k=60, source-tier prior, per-source floors,
  abstention, per-plane injection scan, RecallTrace). Cross-store fused recall@5 = 1.0 (2 local
  planes). + cross-agent leak fix (lineage-scoped + threat-scanned session_search, two-layered).
  Wave-2 review P1 (floor bug) + P2 (lineage hardening) fixed + regression-tested.
- Wave 3: bi-temporal substrate (ext_key, t_valid/t_invalid/supersedes_id, source_store;
  invalidate/supersede never delete) + reconcile engine + ingest redaction (secrets provably not
  stored; knowledge-update supersedes not deletes; idempotent op-queue). Review confirmed NO
  data-loss path. P0 (migration brick) + P1 (3 redaction leaks) fixed + verified.
- Web-grounded validation (2026 SOTA): architecture is on the production mainline; we are SAFER
  than Mem0 (invalidate-not-delete). Verdict + build queue saved.
- Recovery: full work recovered from a shared-tree clobber, isolated in this worktree.
- Backup taken: ~/.hermes/backups/memory-audit-20260620-111243 (state.db, configs). Nothing in
  live ~/.hermes mutated.
- Baseline: 329 passed, 3 skipped (the 3 skips = weak-signal injection shapes, owed to the
  injection-hardening wave).

## IN FLIGHT

- Safety wave wf_9f40b698: A-MemGuard floor inversion fix (floor only provenance-trusted sources
  + consensus suppression of un-corroborated untrusted sole-source). memory_merge.py.

## OPEN (Part 1, from the web build queue, ordered)

1. Tamper-evident provenance (SHA-256 + agent-self-signature) vs MemoryGraft. [SAFETY]
2. Weak-signal injection test suite (MemoryGraft / MINJA / policy-conformant fabricated fact);
   un-skip the 3 skipped tests by making them real. [SAFETY, req #11]
3. Ship dream-ingest fence + cross-feed review_mode before Honcho/GBrain return (live hole). [SAFETY]
4. Gold set to >=40 labeled pairs + BEAM contradiction/event-ordering cases + k-sweep {20,30,40,60}.
5. Promotion edge (outcome-gated + grounding pointer) + the "nothing else promotes" test (req #10).
6. Memory Supervisor (req #12a) wired to the MergeLayer down/blocked/timed-out trace.
7. Remote planes (Honcho recallMode:tools + get_context only, never peer.chat hot-path; GBrain),
   cost-capped. Needs user go for paid bring-up (O-1).
8. The runnable proof script (req #6) + final recall@k/precision@k numbers (req #7).
9. Retention/compaction real path (req #9): raw to summaries to patterns to lessons.

## OPEN (Part 2, observability + self-improvement, the honest version)

P2-0. RECON FIRST (in flight): verify Curator, self-improvement loop, idle-dreaming-fork,
  /rollback+checkpoint+backups, memory-providers (Honcho representation), native
  telemetry/Langfuse/OTel hook, AGENTS.md, version, against the REAL repo + Nous docs. Identify
  the GENUINE GAP. Build only that.
P2-1. Tracing: native hook if it exists, else Langfuse SDK. Per run: goal, system prompt, memory
  + retrieval hits, tool/MCP calls, model calls, tokens, cost, latency, output, user feedback.
P2-2. Evaluator pass scoring completed runs; store scores against traces.
P2-3. Close the loop through the EXISTING curator + self-improvement nudges (no parallel system).
P2-4. Dreaming = scheduled reflection PROPOSAL queue (idle-fork), never auto-applies; versioned,
  reversible, approved.
P2-5. Memory layers map: semantic (USER.md + Honcho), episodic (traces), procedural (skills) +
  utility scoring (used+helpful promoted, unused decays).
P2-6 (extensions): skill metrics layer (success_rate, avg_latency, cost_per_run, user_rating on
  Curator telemetry) + skill health view; skill versioning + A/B; reward signals from real
  feedback; light user knowledge-graph (check Honcho first); real context-compression path.

## OUT OF SCOPE (flagged, not building)

The autonomous fine-tuning dataset + SFT/DPO/RLHF pipeline. Collecting clean traces is fine;
building a training pipeline is future research. STOP at trace collection.

## LOOP BUDGET

Per loop: bounded waves with self-healing (max 3 attempts each), real-ground-truth verification,
commit after each. Stop conditions: definition of done proven, or blocked (needs user / paid
bring-up), or budget hit. Two no-progress iterations = hang, stop and report. Recoverable errors
retried with a CHANGED approach; hard blockers surfaced.
