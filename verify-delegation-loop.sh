#!/usr/bin/env bash
# Reproducible end-to-end proof of the coding-router delegation loop:
#   PLAN (Claude Code, read-only) -> EXECUTE (Codex/fallback) -> REVIEW (Claude Code,
#   read-only, its second hat) -> VERIFY (router runs the tests)
#
# The executor tries Codex first; if Codex cannot run (e.g. the ChatGPT-account model
# gate), it falls back to Claude Code with write tools, per the swe-delegation skill's
# "Executor fallback" section. Either way the loop completes and the test must pass.
#
# Requires: claude (authed), python3; codex optional. Runs in a throwaway git repo.
set -uo pipefail
export PATH="/Users/saksham/.hermes/node/bin:$PATH"

WORK="$(mktemp -d)"; cd "$WORK"; git init -q; git config user.email t@t.co; git config user.name t
echo "== workdir: $WORK =="

echo "== STEP 1: PLAN (Claude Code, read-only — must write NO files) =="
claude -p 'Output the COMPLETE plan as your final message: create stringutil.py with shout(s) returning s.upper()+"!" and test_stringutil.py (pytest) asserting shout("hi")=="HI!". Give exact full file contents. Do NOT create files.' \
  --model sonnet --allowedTools 'Read Glob Grep' --output-format json --max-turns 4 > plan.json 2>/dev/null
PLAN="$(python3 -c "import json;print(json.load(open('plan.json')).get('result') or '')")"
echo "   plan captured: ${#PLAN} chars; files after plan: $(ls ./*.py 2>/dev/null || echo none-good)"
[ -n "$PLAN" ] || { echo "FAIL: planner returned no plan"; exit 1; }

echo "== STEP 2: EXECUTE =="
EXECUTOR=none
if command -v codex >/dev/null 2>&1; then
  # --sandbox danger-full-access: no sandbox (throwaway repo). The deprecated --full-auto
  # alias is intentionally NOT used here; the production skill uses --sandbox workspace-write.
  perl -e 'alarm shift; exec @ARGV' 150 codex exec --sandbox danger-full-access \
    "Implement exactly this plan, create the files in the current directory: $PLAN" > codex.out 2>&1
  ls ./*.py >/dev/null 2>&1 && EXECUTOR="codex"
fi
if [ "$EXECUTOR" = none ]; then
  echo "   Codex unavailable/blocked -> falling back to Claude Code as executor"
  claude -p "Implement exactly this plan by creating the files in the current directory: $PLAN" \
    --model sonnet --allowedTools 'Read Edit Write Bash' --output-format json --max-turns 12 > exec.json 2>/dev/null
  ls ./*.py >/dev/null 2>&1 && EXECUTOR="claude(fallback)"
fi
echo "   executor: $EXECUTOR; files: $(ls ./*.py 2>/dev/null | tr '\n' ' ')"
[ "$EXECUTOR" != none ] || { echo "FAIL: no executor produced files"; exit 1; }

echo "== STEP 3: REVIEW (Claude Code's second hat — read-only, must write NO files) =="
# Route the executor's diff back to Claude Code for code/security/QA review. This proves
# the reviewer stage runs and returns a verdict; the objective test gate (STEP 4) is what
# actually decides pass/fail. In the live loop, a REVISE verdict routes back to the executor.
git add -A >/dev/null 2>&1
git --no-pager diff --cached | claude -p 'Review this diff against the plan for correctness, bugs, SECURITY issues, and QA gaps (missing/weak tests, unhandled edges). Output a one-line VERDICT (PASS or REVISE) then the findings. Do NOT edit any files.' \
  --model sonnet --allowedTools 'Read Glob Grep' --disallowedTools 'Write Edit Bash' --output-format json --max-turns 4 > review.json 2>/dev/null
REVIEW="$(python3 -c "import json;print(json.load(open('review.json')).get('result') or '')" 2>/dev/null)"
echo "   reviewer ran: ${#REVIEW} chars; verdict: $(printf '%s' "$REVIEW" | tr -d '\n' | head -c 90)"
[ -n "$REVIEW" ] || echo "   (reviewer returned no text — non-fatal; the test gate below is authoritative)"

echo "== STEP 4: VERIFY (router runs the tests — the objective gate) =="
if python3 -m pytest -q 2>&1 | tail -3; then
  echo "PASS: delegation loop completed end to end (plan -> execute=$EXECUTOR -> review=claude-code -> verify)"
else
  echo "FAIL: verification did not pass"; exit 1
fi
