# Final Summary - OpenComputer v2 Memory Mission (brutally honest)

Date 2026-06-21. The honest answer to "are you sure you finished everything?": NO, not 100
percent. Here is exactly what is PROVEN LIVE, what is DEPLOYED-BUT-BLOCKED, and what is PENDING.
No spin. No em dashes.

## PROVEN LIVE (real evidence, committed)

1. The LOCAL memory subsystem works through a REAL agent turn. This was the central deliverable
   and the thing the user kept (correctly) pushing on. A real `hermes -z` turn via OC-router
   (claude-sonnet-4-6) recalls a fact seeded into the holographic plane through the live
   MergeLayer and the model USES it. Proof: `docs/memory-audit/proof/prove_live_recall.sh`
   (4/4 deterministic). Two real bugs were caught by proving-it-live and fixed:
   - The MergeLayer attach lived only in init_agent, which the oneshot + gateway paths BYPASS
     (so recall never fired at runtime). Fixed: a shared `wire_memory_merge_planes` helper called
     from all three paths. Commit 7bb4d10ec.
   - A safety-tuned model REFUSED its own recalled memory as "prompt injection" because of the
     "[Treat as authoritative reference data]" framing + the repo AGENTS.md auto-loading. Fixed:
     first-person "[notes you saved to your own memory]" framing (security fence intact) + neutral
     cwd. Commit a1c5445cb.
2. The full subsystem unit/integration suite: ~330 tests green (FTS5, holographic bi-temporal
   store, reconcile, redaction, MergeLayer + RRF + A-MemGuard + provenance, isolation, retention,
   Memory Supervisor, Part 2 observability). The capstone `prove_memory.py`: 10/10 local
   mechanisms.
3. GBrain memory backend PROVEN LIVE on the provisioned VM (oc-ebcf319a, 100.124.164.84). It runs
   as an auto-started systemd service `gbrain.service` (PGLite, HTTP MCP on :3131, health ok), and
   store->keyword-search was proven on the VM (wrote a page, searched a token, got it back). Zero
   Docker, zero OpenRouter (OC-router has no embeddings so it runs tsvector/keyword mode).
4. Honcho memory backend PROVEN LIVE on the VM (the user's primary ask). The full stack runs as 4
   Docker containers (api + deriver on host network for egress, postgres/pgvector + redis), behind
   an auto-started systemd unit `honcho.service` (enabled, survives reboot). `/health` ok, and a
   REAL storage roundtrip was proven on the VM: created workspace/peer/session (201), stored a
   message (201), read it back with the token present (200, True). The deriver (LLM reasoning
   worker) is up and connected to redis, deriving via OC-router (chat -> claude-haiku-4-5;
   embeddings off since OC-router has none). Getting here required fixing real VM infra: Docker
   daemon DNS (8.8.8.8), BuildKit `--network=host` for build-step DNS, a 3GB swapfile (persisted)
   for RAM headroom, and finally `network_mode: host` for api+deriver to escape a Tailscale+Docker
   custom-bridge egress block (172.18.x could not reach the internet; the host network can). The
   honcho/.env routes ALL chat through OC-router (router.tryopencomputer.com), never OpenRouter,
   per the user instruction. Backups of every config taken before changes.

   So the user's vision is realized on the VM: a provisioned VM now auto-starts the memory backends
   (GBrain + Honcho) plus the existing sandbox container, all chat via OC-router. The live agent
   (hermes-gateway), GBrain, and Honcho are all `active`/healthy; nothing was broken.

## PENDING (not done, honest)

5. Deploying the LOCAL subsystem code to the VM's agent. The VM runs hermes 0.16.0 (pip install at
   /usr/local/lib/python3.12/dist-packages); my memory work is on the feat-base (283 commits ahead
   of main, a different lineage). Force-overwriting the VM's modified core files (memory_manager,
   agent_init, oneshot, api_server) risks breaking the live agent via version skew. So the local
   subsystem is PROVEN LIVE locally but NOT yet deployed to the VM. Safe path: port the additive
   changes onto 0.16.0, or move the VM to the feat-base. This is the "final integration" question.
6. Wiring the VM's existing 0.16.0 agent to USE GBrain/Honcho. GBrain runs but the agent's
   mcp_servers.gbrain needs a minted OAuth /mcp token; honcho needs the egress fix first.
7. Phase 6 skeptic final review: died twice on session limits; not completed.
8. Part 2 Langfuse Slices 1-2 are built default-OFF; the enablement (O-P2-1) is a user policy call.

## What the user must decide / do

- O-2: how to deploy the LOCAL subsystem (MergeLayer/holographic/reconcile enhancement) to the VM
  agent. The VM runs hermes 0.16.0; my code is on the feat-base. Port the additive changes onto
  0.16.0, or upgrade the VM to the feat-base. The subsystem is PROVEN LIVE locally; this is the
  remaining deployment step.
- O-3: wire the VM's 0.16.0 agent to actively USE the now-live GBrain + Honcho backends (set
  memory.provider: honcho + mcp_servers.gbrain with a minted token). The backends are up; this
  flips the agent to consume them. A real behavior change on the live agent, so left for your go.
- O-P2-1: Langfuse default-on vs strictly opt-in (the code is built default-off).

## Honest bottom line

The CORE mission (a real combine-on-read memory subsystem, proven working in a live agent turn) is
DONE and PROVEN LIVE. BOTH memory backends are now LIVE and auto-started on the provisioned VM:
GBrain (systemd service) and Honcho (4-container stack + systemd unit, storage roundtrip proven),
all chat routed through OC-router. The user's "VM auto-starts a memory + sandbox container" vision
is realized.

What is genuinely NOT finished: (1) deploying the LOCAL subsystem ENHANCEMENT to the VM agent
(version skew: VM is 0.16.0, code is feat-base) and flipping the live agent to consume the backends
(a behavior change I left for your go); (2) the Phase 6 skeptic final review (died on session
limits). So I am NOT claiming 100 percent end-to-end on the live agent on the VM; the infrastructure
and the local proof are done, the VM-agent cutover is the honest remainder.
