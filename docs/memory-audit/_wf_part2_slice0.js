export const meta = {
  name: 'part2-local-observability-slice',
  description: 'Memory Part 2 local-only working slice (no Langfuse, no user decision needed): Slice 0 = add nullable agent_id/subagent_id/role to turn_outcomes + thread through engine (which agent produced a good run). Slice 3 = additive skill quality columns (success_rate/avg_latency/cost_per_run/user_rating) on the skill_usage sidecar + attribute turn_score to skills used + a read-only skill-health view. Reuses existing Curator/outcomes substrate, never a parallel system, never auto-prunes. Self-healing.',
  phases: [
    { title: 'Slice0', detail: 'agent_id dimension on turn_outcomes' },
    { title: 'Slice3', detail: 'skill quality columns + attribution + health view' },
    { title: 'Review', detail: 'adversarial: additive, no regression, no auto-prune, no data loss' },
  ],
}

const WT = '/Users/saksham/Vscode/OpenComputerV2/OC-memory'
const DOCS = WT + '/docs/memory-audit'
const PY = WT + '/.venv/bin/python'
const PYRIGHT = 'PATH=/Users/saksham/.nvm/versions/node/v22.22.0/bin:$PATH pyright'
const GROUND = 'Work in the worktree ' + WT + ' (Hermes 0.16.0, branch feat/memory-mission). Venv: ' + PY + '. Pyright: ' + PYRIGHT + ' <file>. READ ' + DOCS + '/PART2-gap-map-and-plan.md (Slices 0 and 3) and ' + DOCS + '/PROGRESS.md. VERIFIED seams: turn_outcomes schema is session_id+turn+turn_score with NO agent_id (plugins/outcomes/store.py:6-16); the store already has an additive migration pattern (PRAGMA table_info guard + ALTER TABLE turn_outcomes ADD COLUMN trajectory at store.py:38-40) - MIRROR it exactly. skill_usage _empty_record (tools/skill_usage.py:460-473) has use_count/view_count/patch_count but NO quality columns. The Curator review prompt FORBIDS using usage as a quality signal (curator.py:391-394) so the new quality columns are a READ-ONLY signal for the human reviewer, NEVER an auto-prune/auto-archive trigger. There are 15 existing tests/plugins/test_outcomes*.py + test_langfuse_plugin.py - DO NOT break them. CRITICAL data-safety (req #2/#3): every migration ADDITIVE (nullable ADD COLUMN guarded by PRAGMA), idempotent, never DROP/DELETE; default None = unchanged behavior; the live ~/.hermes/dreaming/outcomes.db is backed up but your tests must use TEMP dbs, never the live store. No new HERMES_* env vars (config.yaml only). No core tool added. No em dashes.'

const VERIFY = {
  type: 'object', additionalProperties: false,
  required: ['passed', 'commands_run', 'evidence', 'failures', 'files_changed'],
  properties: { passed: { type: 'boolean' }, commands_run: { type: 'array', items: { type: 'string' } }, evidence: { type: 'string' }, failures: { type: 'array', items: { type: 'string' } }, files_changed: { type: 'array', items: { type: 'string' } } },
}

const outcomesBaseline = PY + ' -m pytest -q -p no:cacheprovider --timeout=180 tests/plugins/test_outcomes_store.py tests/plugins/test_outcomes_engine.py tests/plugins/test_outcomes_hooks.py tests/plugins/test_outcomes_composite.py tests/plugins/test_outcomes_staged.py tests/plugins/test_outcomes_turn_id.py tests/plugins/test_outcomes_trajectory.py 2>&1 | tail -3'

async function step(phaseTitle, implementPrompt, verifyPrompt, maxAttempts) {
  let attempt = 0, last = null
  while (attempt < maxAttempts) {
    if (attempt === 0) await agent(implementPrompt, { label: phaseTitle + ':implement', phase: phaseTitle })
    else await agent(GROUND + '\n\nPrevious attempt FAILED.\nFailures: ' + JSON.stringify(last && last.failures) + '\nEvidence: ' + (last && last.evidence) + '\nFix the root cause; do not restart. ' + implementPrompt, { label: phaseTitle + ':fix-' + attempt, phase: phaseTitle })
    last = await agent(verifyPrompt, { label: phaseTitle + ':verify-' + attempt, phase: phaseTitle, schema: VERIFY })
    if (last && last.passed) return last
    attempt++
  }
  return last
}

phase('Slice0')
const s0 = await step('Slice0',
  GROUND + '\n\nSLICE 0 (the working slice): add an AGENT/RUN IDENTITY dimension to the outcomes evaluator so "which agent produced a good run" is answerable. In plugins/outcomes/store.py: add nullable columns agent_id TEXT, subagent_id TEXT, role TEXT to turn_outcomes via the SAME additive guarded pattern already used for the trajectory column (PRAGMA table_info check, then ALTER TABLE turn_outcomes ADD COLUMN ...; idempotent; never drop). Update the INSERT/record path to accept and store an optional agent_id/subagent_id/role (default None = unchanged). Add a read seam: a method to fetch recent scores grouped/filtered by agent_id (e.g. recent_scores_by_agent(agent_id) or an agent_id arg on the existing recent_turn_scores), WITHOUT breaking the existing recent_turn_scores / recent_session_scores signatures (additive optional arg only - the existing callers in plugins/dreaming/outcome_link.py and plugins/self_evolution/cycle.py must keep working unchanged). Then thread an optional agent_id through plugins/outcomes/engine.py finalize/stage/record so a turn recorded with an agent context carries it (default None preserves today). Add tests/plugins/test_outcomes_agent_dimension.py proving: (a) the migration adds the columns on a legacy turn_outcomes DB (build one WITHOUT the columns, open the store, assert columns present, existing rows preserved); (b) idempotent (open twice); (c) recording two turns under different agent_ids and reading them back grouped returns the right scores per agent; (d) recording with agent_id=None still works (back-compat); (e) the existing recent_turn_scores still returns the same shape. Keep store.py + engine.py pyright-clean.',
  GROUND + '\n\nVERIFY SLICE 0 with REAL output:\n1) cd ' + WT + ' && ' + PY + ' -m pytest -q -p no:cacheprovider --timeout=120 tests/plugins/test_outcomes_agent_dimension.py 2>&1 | tail -10 (all new tests pass: migration, idempotent, group-by-agent, None back-compat, existing-shape-stable)\n2) pyright: cd ' + WT + ' && ' + PYRIGHT + ' plugins/outcomes/store.py plugins/outcomes/engine.py 2>&1 | tail -1 (0 errors)\n3) outcomes regression (the 15-file suite must not break - run the core 7): cd ' + WT + ' && ' + outcomesBaseline + '\npassed=true ONLY if the new tests pass, pyright clean, and the outcomes regression stays green. Real summary lines in evidence.',
  3)

phase('Slice3')
const s3 = await step('Slice3',
  GROUND + '\n\nSLICE 3 (local skill-health, additive): give each skill outcome-quality metrics WITHOUT a new store and WITHOUT touching auto-prune. (1) In tools/skill_usage.py _empty_record add nullable/zeroed quality fields: success_rate (float, default null/None until first sample), avg_latency_ms (float), cost_per_run (float), user_rating (float), and a sample_count (int, default 0) so rolling averages are computable. Add an additive function record_skill_outcome(skill_name, *, turn_score=None, latency_ms=None, cost=None, user_rating=None) that updates these as a running aggregate (sample_count-weighted mean), atomic-write + flock like the existing bump_* functions. Keep bump_use/bump_view/bump_patch unchanged. (2) Add a read-only skill-health view function skill_health_view() that returns, per skill, the existing counts PLUS the new quality metrics, rankable by most_used/most_successful/most_expensive/most_failing (just return the data + a sort helper; the workspace consumes it). (3) Do NOT call record_skill_outcome from any auto-prune path and do NOT change apply_automatic_transitions - the quality signal is read-only for the human reviewer (honor curator.py:391-394). You MAY add a single read-only line to the curator review render (curator.py around the agent_created_report) that shows the quality metrics IF present, but must not change any transition decision. Add tests/plugins/test_skill_health.py proving: (a) record_skill_outcome updates success_rate as a correct running mean over 3 samples; (b) _empty_record back-compat (loading an old sidecar without the fields yields defaults, no crash); (c) skill_health_view returns the metrics and the 4 sort orders; (d) apply_automatic_transitions behavior is UNCHANGED (no new archival triggered by a low success_rate). Keep skill_usage.py pyright-clean.',
  GROUND + '\n\nVERIFY SLICE 3 with REAL output:\n1) cd ' + WT + ' && ' + PY + ' -m pytest -q -p no:cacheprovider --timeout=120 tests/plugins/test_skill_health.py 2>&1 | tail -10 (running-mean, back-compat, health view + 4 sorts, transitions-unchanged)\n2) pyright: cd ' + WT + ' && ' + PYRIGHT + ' tools/skill_usage.py 2>&1 | tail -1 (0 errors)\n3) regression: cd ' + WT + ' && ' + PY + ' -m pytest -q -p no:cacheprovider --timeout=180 tests/plugins/test_outcomes_store.py tests/plugins/test_outcomes_agent_dimension.py 2>&1 | tail -3 ; and confirm no skill-usage test regressed: ' + PY + ' -m pytest -q -p no:cacheprovider --timeout=120 $(ls tests/**/test_skill*.py tests/**/test_curator*.py 2>/dev/null | tr "\\n" " ") 2>&1 | tail -3\npassed=true ONLY if the new tests pass, pyright clean, and no existing skill/curator/outcomes test regressed. Real output in evidence.',
  3)

phase('Review')
const review = await agent(
  GROUND + '\n\nYou are a skeptical data-safety reviewer of the Part 2 local observability slice (Slice 0 agent_id on turn_outcomes; Slice 3 skill quality columns). Independently verify with ' + PY + ': (a) DATA-SAFETY - build a legacy turn_outcomes DB with rows, run the migration, assert every original row + score survives and the new columns are nullable (no row lost, no DROP); same for the skill sidecar (old .usage.json loads with defaults). (b) ADDITIVE/BACK-COMPAT - the existing recent_turn_scores signature and the dreaming/self_evolution callers still work unchanged; recording with agent_id=None behaves as before. (c) NO AUTO-PRUNE COUPLING - confirm a low success_rate does NOT trigger any archival/transition (grep + a behavioral test); the quality signal is read-only. (d) NO PARALLEL SYSTEM - confirm we reused turn_outcomes + the skill_usage sidecar, did not add a new store or a core tool. Run real commands. Return a concrete verdict + any required fix, and explicitly confirm NO data-loss path and NO auto-prune coupling.',
  { label: 'review:part2-slice', phase: 'Review' })

return { s0_passed: !!(s0 && s0.passed), s3_passed: !!(s3 && s3.passed), s0, s3, review }
