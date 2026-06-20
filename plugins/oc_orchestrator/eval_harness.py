"""Per-agent capability eval harness (structure; the model run is injected).

This is the deterministic scaffolding around a capability eval: given a set of
outcome-asserting cases and an INJECTED ``runner`` callable (the thing that
actually produces an output, e.g. a model-backed agent), it runs each case
``k`` times, applies the case's outcome check to each attempt, and scores two
metrics:

  * pass@k  -- fraction of cases where ANY of the k attempts passed. This is the
    optimistic "best of k" capability number.
  * pass^k  -- fraction of cases where ALL of the k attempts passed. This is the
    pessimistic unattended-reliability number: a tool you leave running needs to
    succeed every time, not just once.

The harness never calls a model itself and never hardcodes a vendor. The model
seam is the ``runner`` parameter, so tests inject a deterministic fake. A check
is a plain ``callable(output) -> bool`` outcome assertion by default (not an
LLM judge), keeping evals cheap and reproducible.

``to_exit_code`` turns a scorecard into a CI gate exit status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List


@dataclass
class EvalCase:
    """One capability case: a prompt plus an outcome assertion on the output."""

    id: str
    prompt: str
    check: Callable[[str], bool]


@dataclass
class CaseResult:
    """Per-case roll-up across the k attempts."""

    case_id: str
    attempts: int
    passes: int
    passed_all: bool
    passed_any: bool


@dataclass
class Scorecard:
    """Aggregate eval result and the gate verdict."""

    total: int
    pass_at_k: float
    pass_pow_k: float
    per_case: List[CaseResult] = field(default_factory=list)
    gate_passed: bool = False


def run_eval(
    cases: List[EvalCase],
    runner: Callable[[str], str],
    *,
    k: int = 1,
    threshold: float = 1.0,
) -> Scorecard:
    """Run each case ``k`` times through ``runner`` and score it.

    ``runner`` is injected (no model is called here): for each case it is invoked
    ``k`` times with the case prompt, and ``case.check`` is applied to each
    output. ``gate_passed`` is true when the unattended-reliability metric
    (pass^k) is at least ``threshold``.
    """
    if k < 1:
        raise ValueError("k must be >= 1")

    per_case: List[CaseResult] = []
    for case in cases:
        passes = 0
        for _ in range(k):
            output = runner(case.prompt)
            if case.check(output):
                passes += 1
        per_case.append(
            CaseResult(
                case_id=case.id,
                attempts=k,
                passes=passes,
                passed_all=(passes == k),
                passed_any=(passes > 0),
            )
        )

    total = len(per_case)
    if total == 0:
        # No cases: vacuously every case passed.
        pass_at_k = 1.0
        pass_pow_k = 1.0
    else:
        pass_at_k = sum(1 for r in per_case if r.passed_any) / total
        pass_pow_k = sum(1 for r in per_case if r.passed_all) / total

    return Scorecard(
        total=total,
        pass_at_k=pass_at_k,
        pass_pow_k=pass_pow_k,
        per_case=per_case,
        gate_passed=pass_pow_k >= threshold,
    )


def to_exit_code(scorecard: Scorecard) -> int:
    """0 when the gate passed, 1 otherwise (for CI gating)."""
    return 0 if scorecard.gate_passed else 1
