# PROGRESS.md — Memory mission persistent state (loop protocol)

The single source of truth for what is done, what failed, what is open. Updated every loop
iteration. Not in CLAUDE.md. No em dashes.

Working dir: `/Users/saksham/Vscode/OpenComputerV2/OC-memory` (git worktree, branch
`feat/memory-mission`, base `feat/agents-orchestrator-mission`). Hermes version pinned: 0.16.0.
Venv: `.venv/bin/python`. Standing rule: commit after every wave (a shared-tree clobber already
ate one uncommitted build; see RECOVERY-NOTE.md). All work isolated in this worktree.

## >>> RESUME HERE (saved before session limit, 2026-06-20) <<<

ALL work is committed and durable. Nothing in flight is lost.

WHERE EVERYTHING LIVES:
- Worktree (canonical, work here): `/Users/saksham/Vscode/OpenComputerV2/OC-memory`, branch
  `feat/memory-mission`, base `feat/agents-orchestrator-mission`, Hermes 0.16.0.
- Venv: `.venv/bin/python` (pytest + pytest-asyncio + aiohttp installed).
- Backup branch (main-based): `feat/memory-subsystem` in the main dir.
- The main dir `/Users/saksham/Vscode/OpenComputerV2/OpenComputerV2` is on `main` (left for the
  other mission). DO NOT work there. Always `cd` to the worktree.
- Last commit: 57bbd4d6c (wip checkpoint). Tree clean. Baseline 329 passed / 3 skipped.

TWO WORKFLOWS WERE IN FLIGHT when the limit hit (resume by run id, completed agents are cached):
- Safety wave (A-MemGuard floor-inversion fix in memory_merge.py): run id `wf_9f40b698-039`,
  script `docs/memory-audit/_wf_safety.js`. On resume: check its task output; if it did not
  finish, re-run `Workflow({scriptPath: ".../_wf_safety.js", resumeFromRunId: "wf_9f40b698-039"})`.
  Memory_merge.py may hold partial edits (committed in the wip). Verify with
  `tests/agent/test_memory_merge.py` and re-run the safety wave if not green.
- Part 2 observability recon: run id `wf_c21a9ce2-c9a`, script `docs/memory-audit/_wf_part2_recon.js`.
  On resume: read its output for the genuine-gap map; if unfinished, resume by run id. It produces
  the Part 2 build plan (reuse Curator/idle-fork/rollback; build only the outcome observability +
  eval gap).

FIRST ACTIONS ON RESUME:
1. `cd /Users/saksham/Vscode/OpenComputerV2/OC-memory` and confirm `git branch --show-current`
   == feat/memory-mission and `git status` clean.
2. Run the baseline: `.venv/bin/python -m pytest -q -p no:cacheprovider --timeout=180 tests/agent/test_memory_merge.py tests/tools/test_holographic_bitemporal.py tests/agent/test_memory_reconcile.py tests/tools/test_search_facts_readonly.py tests/tools/test_memory_recall_eval.py tests/tools/test_session_search_isolation.py` (expect ~all pass; 3 injection skips are known).
3. Check the two in-flight workflow outputs; reconcile/re-run as above.
4. Continue the OPEN queue below (safety items 1-3 first, then promotion, supervisor, remote
   planes, proof script), plus the Part 2 gap plan once the recon lands.

DECISIONS STILL OWED BY THE USER:
- O-1: paid Honcho + GBrain bring-up (Docker + OpenRouter credits) for the remote-planes wave.
- Final integration base: how the memory mission and the agents mission merge into the product.

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

- (none currently; the Part 2 recon is being re-launched after the session-limit failure.)

## DONE since last save

- A-MemGuard floor-inversion fix (web-validation safety item 1): floor protects ONLY
  provenance-trusted sources; un-corroborated untrusted sole-source is consensus-penalized.
  VERIFIED with a real adversarial repro (poisoned untrusted sole-source suppressed from top-8,
  recorded in trace.consensus_penalized=['honcho#p1']; trusted sole-source still floor-protected).
  16 merge tests pass, pyright 0 errors, baseline 126 passed / 3 skipped. Config knobs:
  memory.merge.floor_trusted_sources, memory.merge.consensus_penalty. Committed (57bbd4d6c wip).
  Build-queue safety item 1 (A-MemGuard) DONE.

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

P2-0. RECON DONE -> PART2-gap-map-and-plan.md. KEY FINDING (the honest reframe): Hermes 0.16.0
  ALREADY ships the scorer (plugins/outcomes: turn_score [0,1] composite + aux-LLM judge,
  LIVE-enabled in config), the tracer (plugins/observability/langfuse: per-turn trace, usage/cost,
  opt-in), and the cross-agent hooks (subagent_start/stop in delegate_tool). So DO NOT rebuild
  observability. The genuine gap = JOIN them + ADD agent/run identity.
  REAL GAP, by slice (all additive/reversible, local-first):
    Slice 0 (working slice, S): add nullable agent_id/subagent_id/role to turn_outcomes
      (plugins/outcomes/store.py, same ALTER ADD COLUMN pattern) + thread agent_id through
      engine.py. Unlocks "which agent produces good runs". LOCAL-ONLY, no user decision.
    Slice 1 (S): subscribe subagent_start/stop in langfuse register() + parent child trace. NEEDS
      Langfuse-enablement decision (O-P2-1).
    Slice 2 (M): outcome-to-trace score bridge (call create_score on the matching trace; langfuse
      register has NO create_score today = real gap). NEEDS Langfuse decision.
    Slice 3 (M): skill-outcome attribution + additive success_rate/avg_latency/cost_per_run/
      user_rating on the skill_usage sidecar (_empty_record) -> read-only health signal in the
      curator review render. NEVER wire to auto-prune (curator.py:391-394 forbids usage-as-quality).
      LOCAL-ONLY.
    Slice 4 (M): reflection PROPOSAL pass on the existing idle fork -> writes PROPOSALS.md + the
      HMAC review queue, NEVER auto-applies. LOCAL-ONLY.
    Slice 5 (S-M): explicit feedback -> user_rating; utility="used x helpful" read-only view;
      light controlled entity_type vocab on the holographic entities table.
  Honcho user-graph verdict: do NOT build a separate graph; reuse Honcho peer card + holographic
  entities; only micro-supplement = controlled entity_type vocab. Compression = Part 1 retention #9.
  TWO REAL BUGS found: (a) observer schema-version drift hermes.observer.v1 (middleware.py:17) vs
  opencomputer.observer.v1 (docs README:42); (b) langfuse register() never calls create_score.
  NEW USER DECISIONS: O-P2-1 Langfuse default-on-via-config vs strictly-opt-in (outbound telemetry
  policy; blocks Slices 1-2 going default-on; recommend local-first now, Langfuse opt-in).
  O-P2-2 local-only score view vs Langfuse aggregation (recommend local first). O-P2-3 which
  observer schema string is canonical.
P2-0b OLD (superseded by the above):
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
