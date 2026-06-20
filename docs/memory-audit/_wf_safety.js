export const meta = {
  name: 'memory-safety-floor-provenance',
  description: 'Safety-critical fix from the web validation: the A-MemGuard floor inversion. Gate per-source floors so they protect ONLY provenance-trusted sources (user-authored, signed-self), and add a lightweight retrieval-time consensus check so an un-corroborated untrusted sole-source candidate is suppressed not promoted. memory_merge.py only, self-healing.',
  phases: [
    { title: 'Fix', detail: 'floor gate on trust + consensus suppression' },
    { title: 'Review', detail: 'adversarial: poisoned sole-source suppressed, trusted still protected' },
  ],
}

const WT = '/Users/saksham/Vscode/OpenComputerV2/OC-memory'
const DOCS = WT + '/docs/memory-audit'
const PY = WT + '/.venv/bin/python'
const PYRIGHT = 'PATH=/Users/saksham/.nvm/versions/node/v22.22.0/bin:$PATH pyright'
const GROUND = 'Work in the worktree ' + WT + ' (branch feat/memory-mission). Venv: ' + PY + '. Pyright: ' + PYRIGHT + ' <file>. READ ' + DOCS + '/BUILD-QUEUE-web-deltas.md (items 1 and 3) and ' + DOCS + '/WEB-VALIDATION-verdict.md (section 3 change 1). The MergeLayer is ' + WT + '/agent/memory_merge.py; its tests ' + WT + '/tests/agent/test_memory_merge.py. The candidate envelope carries metadata.source_tier (user_authored / curated / bulk / etc). per_source_floors currently guarantees a sole-source plane is never buried. CRITICAL: that is the A-MemGuard inversion (arXiv 2510.02373) - a poisoned single Honcho/GBrain/cross-fed row is exactly the un-corroborated outlier that must be SUPPRESSED, not floor-protected. Everything stays behind merge.enabled:false. No em dashes. Keep changes surgical and additive; do not break the 329-passed baseline.'

const VERIFY = {
  type: 'object', additionalProperties: false,
  required: ['passed', 'commands_run', 'evidence', 'failures', 'files_changed'],
  properties: { passed: { type: 'boolean' }, commands_run: { type: 'array', items: { type: 'string' } }, evidence: { type: 'string' }, failures: { type: 'array', items: { type: 'string' } }, files_changed: { type: 'array', items: { type: 'string' } } },
}

const baseline = PY + ' -m pytest -q -p no:cacheprovider --timeout=180 tests/agent/test_memory_merge.py tests/tools/test_search_facts_readonly.py tests/tools/test_memory_recall_eval.py tests/tools/test_holographic_bitemporal.py tests/agent/test_memory_reconcile.py tests/tools/test_session_search_isolation.py 2>&1 | tail -3'

phase('Fix')

let attempt = 0, last = null
while (attempt < 3) {
  const prompt = GROUND + '\n\nIMPLEMENT (memory_merge.py only, additive):\n1) TRUST-GATE the per-source floor: define a trusted-source set (default {user_authored, signed_self}) and only apply the per-source floor (the guaranteed slot) to a planes best candidate when that candidate metadata.source_tier is in the trusted set. A plane whose only hits are untrusted (bulk / cross-fed / external / honcho / gbrain) gets NO guaranteed floor slot.\n2) CONSENSUS suppression (lightweight A-MemGuard): a candidate that is (a) untrusted AND (b) sole-source (its normalized text/HRR has no corroborating near-duplicate from any OTHER plane) is DEMOTED below the abstention/normal ranking (multiply its final score by a configurable consensus_penalty, default ~0.5) so it cannot occupy a top slot on its own. A corroborated untrusted candidate (matched by another plane) is NOT penalized. Record in the RecallTrace which candidates were floor-skipped (untrusted) and which were consensus-penalized.\n3) Config knobs with defaults: memory.merge.floor_trusted_sources (list), memory.merge.consensus_penalty (float), both with sensible defaults; behavior unchanged when a deployment opts out.\nUpdate the trace schema/docstring. Add tests in tests/agent/test_memory_merge.py: (a) a poisoned untrusted bulk sole-source candidate is NOT floor-protected and is suppressed below a trusted candidate (the exact A-MemGuard scenario); (b) a user_authored sole-source candidate IS still floor-protected (the original floor still works for trusted sources, so the Wave-2 regression test still passes); (c) an untrusted candidate corroborated by a second plane is NOT penalized. Keep memory_merge.py pyright-clean.' + (attempt === 0 ? '' : '\n\nPrevious attempt FAILED: ' + JSON.stringify(last && last.failures) + '\nEvidence: ' + (last && last.evidence) + '\nFix the root cause; do not restart.')
  await agent(prompt, { label: attempt === 0 ? 'fix:implement' : 'fix:retry-' + attempt, phase: 'Fix' })
  last = await agent(GROUND + '\n\nVERIFY with REAL output:\n1) ' + PY + ' -m pytest -q -p no:cacheprovider --timeout=120 tests/agent/test_memory_merge.py 2>&1 | tail -10 (the 3 new safety tests pass AND the existing Wave-2 floor regression test test_low_tier_sole_source_survives_full_budget_flood still passes for a TRUSTED source)\n2) ' + PYRIGHT + ' agent/memory_merge.py 2>&1 | tail -1 (0 errors)\n3) regression: cd ' + WT + ' && ' + baseline + ' (no regressions)\npassed=true only if the poisoned untrusted sole-source is provably suppressed, trusted floors still work, pyright clean, baseline green.', { label: 'fix:verify-' + attempt, phase: 'Fix', schema: VERIFY })
  if (last && last.passed) break
  attempt++
}

phase('Review')
const review = await agent(GROUND + '\n\nYou are a skeptical security reviewer. Independently reproduce with ' + PY + ': build a MergeLayer scenario where ONE untrusted plane (source_tier bulk, simulating a poisoned cross-fed Honcho row) is the sole source of a high-native-rank candidate, and a trusted plane has the real answer. Confirm the poisoned untrusted sole-source is NOT in the top slots (suppressed), while a genuinely user_authored sole-source IS still floor-protected. Also confirm the change did not weaken normal multi-plane recall. Return a concrete verdict + any required fix.', { label: 'review:safety', phase: 'Review' })

return { passed: !!(last && last.passed), last, review }
