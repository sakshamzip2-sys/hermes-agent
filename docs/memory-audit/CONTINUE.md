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

## IN-FLIGHT AT SAVE TIME (salvaged in the wip commit; re-verify on resume)

- Safety wave 2 (wf_74fe8dc2): weak-signal injection suite (un-skip the 3 skipped shapes +
  MemoryGraft/MINJA cases) + dream cross-feed fence (importer.py). Partial edits to
  memory_reconcile.py, memory_redaction.py, test_memory_injection_suite.py were committed in the
  wip salvage. ON RESUME: check tests/agent/test_memory_injection_suite.py + run the injection
  tests; if incomplete, re-run Workflow({scriptPath: "docs/memory-audit/_wf_safety2.js"}).

## NEXT ACTION (do this first on "continue")

1. Run the 3 verify commands above. If the proof script and baseline are green, the salvaged
   safety-wave-2 work is intact; commit it cleanly (selective add) if not already committed.
2. If safety wave 2 was incomplete: finish it (re-launch _wf_safety2.js), verify, commit.
3. Then the remaining queue (all local-first, no user decision needed):
   - Tamper-evident provenance (SHA-256 + agent-self-signature; store.py schema + MergeLayer
     trust step) -- web-validation safety item 2, made urgent by the forgeable-source_tier finding.
   - Part 2 Slice 4: reflection PROPOSAL pass on the existing idle fork -> writes PROPOSALS.md +
     the HMAC review queue, NEVER auto-applies. (plugins/dreaming + review.py)
   - Part 2 Slice 5: explicit feedback -> user_rating; utility view; entity_type vocab.
   - Capture the proof-script real output into evidence/EVIDENCE-LOG.md.
   - Phase 6: skeptical-staff final review pass; fix top issues; loop until a skeptic approves.
4. Each wave: self-healing (max 3 attempts), real-output verify, SELECTIVE git add + commit.

## WAITING ON THE USER (non-blocking; do local-first meanwhile)

- O-1: paid Honcho + GBrain bring-up (Docker + OpenRouter credits) for the remote-planes proof.
- O-P2-1: enable Langfuse tracing by default via config vs strictly opt-in (outbound telemetry
  policy). Recommend local-first now, Langfuse opt-in. Blocks Part 2 Slices 1-2 going default-on.
- O-P2-3: which observer schema string is canonical (hermes.observer.v1 vs opencomputer.observer.v1).
- Final integration: how the memory mission + the agents mission merge into the product line.

## HOUSE RULES (still apply)

No fabricated evidence (real test output only). Backup before any data change. Hard stop before
irreversible actions. Self-modifications versioned + reversible; dreaming proposes, never applies.
CLAUDE.md never auto-rewritten. No em dashes. Everything behind merge.enabled:false /
default-OFF until proven. Commit after every wave.
