export const meta = {
  name: 'part2-observability-recon',
  description: 'Verify-real-docs-first recon for Memory Part 2 (observability + self-improvement). Maps what Hermes 0.16.0 ALREADY ships (Curator, self-improvement loop, idle-dreaming-fork, rollback/checkpoint/backups, memory-provider Honcho representation, native telemetry/Langfuse/OTel hook) against the real repo + Nous docs, then identifies the GENUINE GAP (cross-agent outcome observability + eval) so we build only that, not a duplicate stack.',
  phases: [
    { title: 'Recon', detail: '6 read-only + web streams verify what already ships' },
    { title: 'GapPlan', detail: 'genuine-gap map + build plan reusing existing substrate' },
  ],
}

const WT = '/Users/saksham/Vscode/OpenComputerV2/OC-memory'
const DOCS = WT + '/docs/memory-audit'
const NOUS = 'https://hermes-agent.nousresearch.com/docs'
const GROUND = 'READ-ONLY recon in the worktree ' + WT + ' (Hermes 0.16.0, branch feat/memory-mission). The user directive (Memory Part 2, honest version): Hermes already ships the Curator (skill analytics + lifecycle), a self-improvement loop (writes a skill after a complex 5+ tool-call task, patches skills in use, prunes on schedule, riskiest rewrites gated behind review), an idle background fork (the dreaming substrate), and /rollback + checkpoint shadow store + backups. DO NOT rebuild these. The GENUINE GAP is cross-agent OUTCOME observability + evaluation (whether a run was actually good), where Langfuse fits. Out of scope: any SFT/DPO/RLHF training pipeline (flag, do not build). You MUST verify against the REAL repo code AND the Nous docs at ' + NOUS + ' (WebFetch the curator, skills, and memory-providers pages, plus any telemetry/observability/Langfuse page). Cite file:line and doc URLs. No em dashes.'

const RECON = {
  type: 'object', additionalProperties: false,
  required: ['component', 'what_it_does', 'reuse_surface', 'gap', 'evidence'],
  properties: {
    component: { type: 'string' },
    what_it_does: { type: 'string', description: 'verified behavior with file:line + doc URLs' },
    reuse_surface: { type: 'array', items: { type: 'string' }, description: 'concrete hooks/tables/functions Part 2 can plug into instead of rebuilding' },
    gap: { type: 'array', items: { type: 'string' }, description: 'what is genuinely missing for Part 2' },
    evidence: { type: 'string' },
  },
}

phase('Recon')

const streams = [
  { id: 'curator', label: 'recon:curator', p: 'Read the Curator / skill-lifecycle code: ' + WT + '/tools/skill_usage.py, ' + WT + '/tools/skill_manager_tool.py, ' + WT + '/tools/skills_tool.py, ' + WT + '/tools/skill_provenance.py, ' + WT + '/tools/skill_run_tool.py, and grep curator config in ~/.hermes/config.yaml. Confirm: it tracks use_count/view_count/patch_count/last_activity_at/state in a skill_usage SQLite table + usage sidecar; stale (30d) to archived (90d) lifecycle, never deletes; aux-model review fork proposing consolidations/patches; pin/archive/restore/prune/backup/rollback; agent-created-only (bundled/hub off-limits). ALSO WebFetch the Nous curator doc. What exact table/columns/functions can Part 2 read to add success_rate, avg_latency, cost_per_run, user_rating WITHOUT a new store? What is genuinely missing (it has usage, not OUTCOME quality).' },
  { id: 'selfimprove', label: 'recon:selfimprove', p: 'Read the self-improvement + idle-fork code: grep ' + WT + '/run_agent.py and ' + WT + '/cli.py for the skill-creation-after-complex-task trigger, skill patching, the prune schedule, and the IDLE BACKGROUND FORK (the dreaming substrate, the same pattern used for memory/skill nudges). Identify the exact function/seam where a scheduled reflection PROPOSAL pass could hook in (idle fork). ALSO WebFetch the Nous skills doc. Confirm the loop exists and where Part 2 plugs in vs forking a parallel system.' },
  { id: 'telemetry', label: 'recon:telemetry', p: 'DECIDE the tracing substrate. Grep ' + WT + ' deeply for langfuse / opentelemetry / otel / a native telemetry or tracing hook (not just the word telemetry). Read what tools/skill_usage.py and hermes_cli/middleware.py actually do with "telemetry". Check for any per-run trace/span emission, a callbacks/hooks system (shell_hooks, lifecycle events), and whether the agent loop already emits structured run records. ALSO WebFetch the Nous docs for any observability/telemetry/Langfuse page. VERDICT: does Hermes have a NATIVE hook to reuse, or must we instrument with the Langfuse SDK, or is there an existing hooks system (e.g. PreToolUse/PostToolUse/Stop lifecycle) we can attach a tracer to? Be concrete about the seam.' },
  { id: 'honcho-rep', label: 'recon:honcho-rep', p: 'For the "knowledge graph of the user" item: verify what Honcho ALREADY gives as user representation/entities BEFORE proposing a separate graph. Read ' + WT + '/plugins/memory/honcho/ (client.py, session.py) for peer card / representation / conclusions / entities, and recall the Nous honcho doc (honcho_profile, honcho_conclude, peer card of <=40 atomic facts, directional representations). WebFetch honcho.dev or the Nous memory-providers page if needed. VERDICT: does Honcho already cover the structured user model (entities + relationships like company/projects/clients/preferences), or is a light supplementary structured layer genuinely needed, and if so what minimal shape (reuse the holographic entities table)?' },
  { id: 'rollback', label: 'recon:rollback', p: 'Read the safety substrate to REUSE (not reinvent): grep ' + WT + ' for /rollback, the checkpoint shadow store, and backups (hermes_cli/backup.py, checkpoints, the curator backup/rollback). Confirm how a versioned + reversible + approved self-modification (the dreaming proposal queue, skill A/B promote) should use these existing mechanisms for safety. Cite file:line.' },
  { id: 'agents-md', label: 'recon:agents-md', p: 'Read ' + WT + '/AGENTS.md sections on the Curator, skills, memory, hooks, and any observability/eval. Plus confirm the version (pyproject 0.16.0) and any Curator/self-improvement config keys in ~/.hermes/config.yaml. Summarize the documented intent + config surface Part 2 must respect, and any explicit policy (e.g. no new core tools, capability at the edges).' },
]

const recon = (await parallel(streams.map(s => () => agent(GROUND + '\n\n' + s.p, { label: s.label, phase: 'Recon', schema: RECON })))).filter(Boolean)
const reconJson = JSON.stringify(recon)

phase('GapPlan')

const plan = await agent(GROUND + '\n\nWrite the PART 2 GENUINE-GAP MAP + BUILD PLAN. Inputs: 6 recon streams (JSON):\n' + reconJson + '\n\nAlso read ' + DOCS + '/PROGRESS.md (Part 2 items P2-0..P2-6) and ' + DOCS + '/BUILD-QUEUE-web-deltas.md. Markdown, no em dashes. Structure:\n1. What Hermes 0.16.0 ALREADY ships for self-improvement (Curator, self-improvement loop, idle-fork, rollback) with the exact reuse surface (tables/functions/hooks), so we do NOT rebuild it.\n2. The tracing decision: native hook vs existing hooks-system vs Langfuse SDK, with the concrete seam (file/function) and why. Default to the cheapest reuse.\n3. The GENUINE GAP, item by item, mapping each Part 2 + extension item (P2-1 tracing, P2-2 evaluator, P2-3 close-loop-via-curator, P2-4 reflection-proposal-queue, P2-5 memory-layer utility scoring, P2-6 skill-metrics/A-B/reward-signals/user-graph/compression) to: ALREADY-EXISTS-REUSE / GENUINE-GAP-BUILD / OUT-OF-SCOPE, each with the exact seam and effort S/M/L.\n3b. Honcho user-representation verdict: separate user-graph or reuse Honcho + holographic entities? Decide.\n4. A phased, reversible build plan (working slice first) that REUSES the curator/idle-fork/rollback, never forks a parallel system, and where dreaming PROPOSES to PROPOSALS.md never auto-applies. Each step independently testable with real evidence.\n5. Explicitly flag the SFT/DPO/RLHF pipeline as OUT OF SCOPE (collect clean traces, stop there).\n6. Open questions for the user. Return ONLY the markdown.', { label: 'plan:part2-gap', phase: 'GapPlan' })

return { plan, n_recon: recon.length }
