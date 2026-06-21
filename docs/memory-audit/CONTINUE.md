# CONTINUE.md — read THIS ONE FILE to resume the memory mission (token-cheap)

On a fresh session, when the user says "continue": read this file + `git log --oneline -15` +
the bottom of `PROGRESS.md`. Do NOT re-read the conversation. Run the baseline, then continue
from "NEXT ACTION". That is all the context you need. No em dashes.

## Where everything is

- Worktree (work HERE, never the main dir): `/Users/saksham/Vscode/OpenComputerV2/OC-memory`
- Branch: `feat/memory-mission` (base `feat/agents-orchestrator-mission`). Hermes 0.16.0.
- Venv: `.venv/bin/python` (pytest, pytest-asyncio, aiohttp installed). Pyright:
  `PATH=/Users/saksham/.nvm/versions/node/v22.22.0/bin:$PATH pyright <file>`
- Full audit + design + plans: `docs/memory-audit/*.md`. State: `PROGRESS.md`. Learned-rule
  queue (needs user approval, never auto-applied): `PROPOSALS.md`.
- The OTHER mission (Specialized Agents) shares the SAME repo and once clobbered this work; that
  is why we are in an isolated worktree. NEVER work in the main dir. Commit after every wave.

## First 3 commands on resume (verify ground truth, do not trust this file blindly)

```
cd /Users/saksham/Vscode/OpenComputerV2/OC-memory && git branch --show-current && git status --short
.venv/bin/python docs/memory-audit/proof/prove_memory.py        # the capstone proof (req #6)
.venv/bin/python -m pytest -q -p no:cacheprovider --timeout=180 \
  tests/agent/test_memory_merge.py tests/tools/test_holographic_bitemporal.py \
  tests/agent/test_memory_reconcile.py tests/tools/test_search_facts_readonly.py \
  tests/tools/test_memory_recall_eval.py tests/tools/test_session_search_isolation.py \
  tests/plugins/test_outcomes_agent_dimension.py tests/plugins/test_skill_health.py
```

## DONE + COMMITTED (each line = a verified wave; see git log for the commit)

- Phase 1 recon + per-component verdict (real evidence E1-E12 in evidence/EVIDENCE-LOG.md):
  session FTS5 WORKING; holographic WORKING; Honcho code-correct/server-down; GBrain engine
  WORKING/server-down; gateway isolation WORKING; delegate-channel leak (C-4) found + FIXED.
- Phase 2 cited best-practices brief. Phase 3 locked design + orchestration spec (RMS + BEOH).
- Slice 1: retrieval eval + frozen gold set (req #7). OR recall@5 = 1.0 vs NL 0.375.
- Wave 2: MergeLayer (parallel fan-out + RRF k=60 + source-tier prior + per-source floors +
  abstention + per-plane injection scan + RecallTrace). Cross-store fused recall@5 = 1.0.
  + cross-agent leak fix (lineage-scoped + threat-scanned session_search, two-layered).
- Wave 3: bi-temporal substrate (invalidate/supersede never delete) + reconcile engine + ingest
  redaction. Review: NO data-loss path. P0 migration-brick + P1 redaction-leaks fixed.
- A-MemGuard floor-inversion fix (web-validation safety item 1): floor only provenance-trusted
  sources + consensus-suppress un-corroborated untrusted sole-source. Verified by adversarial
  repro. (Surfaced: source_tier is a forgeable tag -> tamper-evident provenance is next.)
- Part 2 (honest version): recon proved Hermes already ships the scorer (outcomes/turn_score),
  the tracer (langfuse plugin), and cross-agent hooks. Built ONLY the gap: Slice 0 = agent_id on
  turn_outcomes ("which agent produced a good run"); Slice 3 = skill quality columns + read-only
  health view (never auto-prunes). Reused the Curator substrate; no parallel system.
- Proof script req #6 written (proof/prove_memory.py). Dreaming federated-tree idea evaluated ->
  flatten verdict. Dreaming amplification plan. Web-validation verdict (we are on 2026 mainline,
  safer than Mem0). All in docs/memory-audit/.
- Backups (req #2): ~/.hermes/backups/memory-audit-20260620-111243 (state.db) and
  memory-audit-part2-20260620-161444 (outcomes.db 92 rows). Nothing in live ~/.hermes mutated.

## PROVEN since last save (all committed; full integrated baseline 147 passed)

- CAPSTONE proof (req #6): `docs/memory-audit/proof/prove_memory.py` -> 10/10 LOCAL mechanisms
  PASS. Real output saved as evidence E13.
- Safety wave 2 COMPLETE: WeakSignal (injection suite 19 passed; 3 shapes un-skipped) +
  DreamFence (cross-feed fence: sanitize+strict-scan+redact, fail-closed, review_mode queues to
  HMAC review not MEMORY.md; 6 fence + 130 dream tests). Committed 01643948d.
- Tamper-evident provenance (web-validation safety item 2): SHA-256 content_hash + HMAC signature
  on the holographic store; MergeTrust gate requires verifiable provenance for a trusted tier ->
  forged cross-fed source_tier=user_authored is downgraded to untrusted + consensus-suppressed.
  Committed 3ba68c591.

## HARD PRECONDITION for the remote-planes wave (O-1) -- do NOT wire remote cross-feed write until fixed

- LATENT write-side hole (found by the provenance review, NOT live today): _maybe_sign in
  plugins/memory/holographic/store.py trusts the caller-supplied source_store, so a future
  cross-feed WRITE could self-sign by passing source_store="orchestrator/self" and become
  "trusted". Not exploitable now (remote write is a QUEUE_REMOTE stub; reconcile writes only
  local content). FIX before remote write is wired: signing eligibility must require an explicit
  self-generated signal from the caller (e.g. add_fact(sign_as_self=True) set ONLY by the
  orchestrator's own reconcile path), not be inferable from the source_store string; the remote
  ingest path must route to a remote namespace (honcho/*, gbrain/*) and never self-sign.

## ALSO PROVEN since (committed; full baseline 158 passed, proof still 10/10)

- Part 2 Slice 4: reflection PROPOSAL pass on the idle fork -> PROPOSALS.md + HMAC review queue,
  proven to NEVER edit a skill/prompt/memory/fact store (author 11 tests + independent reviewer
  29/29 via full-tree-diff + write instrumentation). Default OFF. Committed e79acc776.

## ALSO PROVEN since (committed; full baseline 182 passed)

- Part 2 Slice 5: feedback reward signals -> user_rating; read-only utility view (used x helpful);
  controlled entity_type vocab on the holographic entities (reuse Honcho + holographic, no
  separate graph). 24 tests. Committed 2d23be873. PART 2 LOCAL SLICES (0,3,4,5) ALL DONE; only
  Slices 1-2 (Langfuse trace-linkage + score bridge) remain, blocked on O-P2-1.

## ALSO PROVEN since (committed; full baseline 201 passed)

- Retention + tiered compaction (req #9): archive-not-delete eviction (archived_at, reversible)
  + raw->summary->pattern->lesson fold with source provenance + signed summaries + bounded
  growth, on the idle fork, default OFF. 19 tests. Review: NO hard-DELETE, NO data loss.
  Committed f87222675. ALL PART 1 + PART 2 LOCAL BUILD ITEMS ARE DONE.

## HONEST RE-SCOPE (2026-06-21): see GAPS.md. Use OC-router for models, NOT OpenRouter.

The user called out that the subsystem was built but NOT wired into the live agent. GAPS.md is the
brutal list. OC-router (router.tryopencomputer.com, serving claude models) removed the money
blocker: GBrain/Honcho chat route through it; GBrain runs tsvector/keyword (no OC-router
embeddings). NO paid OpenRouter needed.

## GAPS CLOSED + COMMITTED (verified)

- GAP-3 Memory Supervisor (req #12a): 2c02a14a5. 22 tests + breaker smoke.
- GAP-5 Langfuse Slices 1-2 (default-off): aa87558e1 + 3294eb69e (entanglement fix). 58 tests.
- GAP-1/2/4/9 first live-wiring pass: 3efab18bf. MergeLayer in prefetch_all, reconcile worker,
  E2E test. 295 baseline. BUT see the RE-OPEN below.
- GAP-7/8 write-side signing hole + retention revive: 52b2148a4. 88 tests + independent review.
- GBrain proven live (no money): PGLite tsvector store+search + serve --http on :3131 healthy.

## CRITICAL IN-FLIGHT: GAP-1 live wiring was HALF-DONE (proven-live caught it)

A REAL `hermes -z` turn via OC-router does NOT recall a seeded holographic fact: the MergeLayer
attach was wired ONLY into agent_init.init_agent, but hermes_cli/oneshot.py BYPASSES init_agent
(constructs AIAgent directly) so the attach never fires at runtime. The MergeLayer + prefetch_all
work when attached (proven manually). FIX wave wf_c6b90d7d (re-running after a session-limit
death; the dead run's partial agent_init helper was REVERTED to clean HEAD): extract a shared
wire_memory_merge_planes() helper called from init_agent + oneshot + gateway; PROOF GATE = a real
hermes -z turn recalls /etc/oc/zephyr-quoll-7731.key via OC-router. Script
docs/memory-audit/_wf_livewire_fix.js (resumeFromRunId works).

## NEXT ACTION (do this first on "continue")

1. cd to the worktree; git status; run the proof script + baseline (the 3 commands above).
2. Check the live-wiring-fix wave (wf_c6b90d7d) output / re-run _wf_livewire_fix.js. The hard gate:
   `bash docs/memory-audit/proof/prove_live_recall.sh` must print the recalled path from a REAL
   hermes -z turn. SELECTIVE-commit agent_init.py + hermes_cli/oneshot.py + gateway api_server.py
   + the test once the LIVE recall genuinely works.
3. Bring up Honcho (Docker, chat via OC-router) + prove connectivity/storage; GBrain already proven.
4. Re-run Phase 6 skeptic review (wf_phase6.js, died twice on limit) -> punch-list -> fix must-fixes.
5. FINAL SUMMARY deliverable; flip this file to "DEFINITION OF DONE MET + proven live".
6. Each wave: self-healing, REAL-output verify (esp. the live hermes -z recall), SELECTIVE commit.

## WAITING ON THE USER (only these; everything else is autonomous now)

- O-P2-1: enable Langfuse tracing by default via config vs strictly opt-in (outbound telemetry
  policy). Code is DONE default-off; only the enablement policy is yours.
- O-P2-3: which observer schema string is canonical (hermes.observer.v1 vs opencomputer.observer.v1).
- Final integration: how the memory mission + the agents mission merge into the product line.

## HOUSE RULES (still apply)

No fabricated evidence (real test output only). Backup before any data change. Hard stop before
irreversible actions. Self-modifications versioned + reversible; dreaming proposes, never applies.
CLAUDE.md never auto-rewritten. No em dashes. Everything behind merge.enabled:false /
default-OFF until proven. Commit after every wave.
