"""Tests for the per-agent capability eval harness.

The model run is INJECTED as a deterministic ``runner`` callable, so these
tests exercise the real harness logic (pass@k vs pass^k accounting, gating,
exit-code mapping) with no model and no mocks of the harness itself.
"""

from __future__ import annotations

from plugins.oc_orchestrator.eval_harness import (
    CaseResult,
    EvalCase,
    Scorecard,
    run_eval,
    to_exit_code,
)


def _cases():
    """Two real outcome-asserting cases (the check is a callable on output)."""
    return [
        EvalCase(id="echo", prompt="say hi", check=lambda out: "hi" in out),
        EvalCase(id="contains", prompt="mention foo", check=lambda out: "foo" in out),
    ]


def test_always_pass_runner_full_score_and_gate_passes():
    # A runner that always returns an output satisfying both checks.
    def runner(prompt: str) -> str:
        return "hi foo"

    card = run_eval(_cases(), runner, k=3, threshold=1.0)

    assert isinstance(card, Scorecard)
    assert card.total == 2
    assert card.pass_at_k == 1.0
    assert card.pass_pow_k == 1.0
    assert card.gate_passed is True
    assert to_exit_code(card) == 0


def test_flaky_runner_high_pass_at_k_low_pass_pow_k_gate_fails():
    # Runner passes exactly 1 of every k attempts (first attempt only).
    state = {"n": 0}

    def runner(prompt: str) -> str:
        state["n"] += 1
        # Pass on the first attempt of each case, fail afterwards.
        return "hi foo" if state["n"] % 3 == 1 else "WRONG"

    cases = [
        EvalCase(id="a", prompt="p", check=lambda out: out == "hi foo"),
        EvalCase(id="b", prompt="p", check=lambda out: out == "hi foo"),
    ]
    card = run_eval(cases, runner, k=3, threshold=1.0)

    # ANY of k passed for both cases -> pass@k == 1.0
    assert card.pass_at_k == 1.0
    # ALL of k passed for neither case -> pass^k == 0.0
    assert card.pass_pow_k == 0.0
    # Gate at threshold 1.0 on the unattended-reliability metric fails.
    assert card.gate_passed is False
    assert to_exit_code(card) == 1


def test_per_case_results_are_correct():
    # Case "good" passes on all k; case "bad" never passes.
    def runner(prompt: str) -> str:
        return "ok" if prompt == "good" else "no"

    cases = [
        EvalCase(id="good", prompt="good", check=lambda out: out == "ok"),
        EvalCase(id="bad", prompt="bad", check=lambda out: out == "ok"),
    ]
    card = run_eval(cases, runner, k=4, threshold=1.0)

    by_id = {r.case_id: r for r in card.per_case}
    assert set(by_id) == {"good", "bad"}

    good = by_id["good"]
    assert isinstance(good, CaseResult)
    assert good.attempts == 4
    assert good.passes == 4
    assert good.passed_all is True
    assert good.passed_any is True

    bad = by_id["bad"]
    assert bad.attempts == 4
    assert bad.passes == 0
    assert bad.passed_all is False
    assert bad.passed_any is False

    # pass@k = fraction of cases with ANY pass = 1 of 2; pass^k = ALL = 1 of 2.
    assert card.pass_at_k == 0.5
    assert card.pass_pow_k == 0.5
    assert card.gate_passed is False  # 0.5 < 1.0


def test_to_exit_code_maps_gate():
    passing = Scorecard(
        total=1,
        pass_at_k=1.0,
        pass_pow_k=1.0,
        per_case=[],
        gate_passed=True,
    )
    failing = Scorecard(
        total=1,
        pass_at_k=1.0,
        pass_pow_k=0.0,
        per_case=[],
        gate_passed=False,
    )
    assert to_exit_code(passing) == 0
    assert to_exit_code(failing) == 1


def test_threshold_below_one_can_pass_with_partial_reliability():
    # 1 of 2 cases reliably passes all k -> pass^k == 0.5; gate at 0.5 passes.
    def runner(prompt: str) -> str:
        return "ok" if prompt == "good" else "no"

    cases = [
        EvalCase(id="good", prompt="good", check=lambda out: out == "ok"),
        EvalCase(id="bad", prompt="bad", check=lambda out: out == "ok"),
    ]
    card = run_eval(cases, runner, k=2, threshold=0.5)

    assert card.pass_pow_k == 0.5
    assert card.gate_passed is True
    assert to_exit_code(card) == 0


def test_empty_cases_yields_zero_total_and_perfect_fractions():
    def runner(prompt: str) -> str:
        return "anything"

    card = run_eval([], runner, k=2, threshold=1.0)
    assert card.total == 0
    # Vacuously, every (zero) case passed -> fractions are 1.0, gate passes.
    assert card.pass_at_k == 1.0
    assert card.pass_pow_k == 1.0
    assert card.gate_passed is True
