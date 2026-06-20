#!/usr/bin/env bash
# One command that demonstrates BOTH features (Specialized Agents + Parallel
# Agents) and the Orchestrator end to end, with real evidence (guardrail 7).
#
# Runs the full mission test suite, then every live demo against the running
# gateway. Exits non-zero if anything fails. Run from the repo root:
#     bash scripts/demo_all.sh
#
# Prereq for the LIVE demos: the gateway up on :8642 with a reachable model
# (the test suite alone needs no gateway/model).

set -u
cd "$(dirname "$0")/.."
VENV="./.venv/bin/python"
PASS=0; FAIL=0
run() { echo; echo "### $1"; if eval "$2"; then echo "[OK] $1"; PASS=$((PASS+1)); else echo "[FAIL] $1"; FAIL=$((FAIL+1)); fi; }

echo "================ MISSION TEST SUITE (no gateway needed) ================"
run "test suite (33 files)" "scripts/run_tests.sh \
  tests/test_run_event_spine.py tests/test_run_outbox_drainer.py tests/test_run_reconciler.py tests/test_reconciler_adversarial.py \
  tests/test_oc_agents_spine_integration.py tests/test_oc_orchestrator_caps.py tests/test_oc_orchestrator_recovery.py \
  tests/test_oc_orchestrator_spine_bridge.py tests/test_oc_orchestrator_selfheal.py tests/test_oc_orchestrator_driver.py \
  tests/test_oc_orchestrator_kanban.py tests/test_oc_orchestrator_router.py tests/test_oc_orchestrator_decompose.py tests/test_oc_orchestrator_brain.py \
  tests/test_eval_harness.py tests/test_sse_tailer.py tests/test_sse_endpoint.py tests/test_feeder.py \
  tests/test_parallel_view_projection.py tests/test_parallel_view_concurrency_sim.py \
  tests/test_agent_manifests.py tests/test_coder_profile.py tests/test_finance_profile.py \
  tests/test_spine_adversarial.py tests/test_caps_recovery_adversarial.py tests/test_router_decompose_adversarial.py tests/test_projection_tailer_adversarial.py \
  2>&1 | tail -1"

echo
echo "================ LIVE DEMOS (need the gateway + a model) ================"
run "Feature B: truth-under-failure spine (real SIGKILL)" "$VENV scripts/demo_parallel_view.py >/dev/null 2>&1"
run "Orchestrator: recovery (isolated)" "$VENV scripts/demo_orchestrator_recovery.py >/dev/null 2>&1"
run "Orchestrator: LIVE recovery (real gateway dispatch)" "HERMES_HOME=\$HOME/.hermes $VENV scripts/demo_orchestrator_live.py >/dev/null 2>&1"
run "Teams: live coordination (real DB)" "$VENV scripts/demo_live_team.py >/dev/null 2>&1"
run "Feature A: per-agent capability evals (live model)" "$VENV scripts/run_agent_evals.py >/dev/null 2>&1"
run "Orchestrator: live LLM brain route+decompose" "HERMES_HOME=\$HOME/.hermes $VENV scripts/demo_brain_live.py >/dev/null 2>&1"

echo
echo "================ SUMMARY ================"
echo "passed=$PASS failed=$FAIL"
echo "To watch the live cockpit: open http://localhost:3000/app/parallel-agents"
[ "$FAIL" -eq 0 ] && echo "ALL GREEN" || echo "SOME FAILED (see above)"
exit "$FAIL"
