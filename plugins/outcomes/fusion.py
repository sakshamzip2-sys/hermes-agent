"""Fused turn_score — combine composite + optional judge into the final score.

Ported from OpenComputer v1 (``evolution/score_fusion.py``). The judge dominates
(0.6) because it reads trajectory semantics (e.g. the *severity* of a correction);
the composite (0.4) anchors when the judge is biased. When the judge is unavailable
(cost guard, missing provider, parse failure), the score falls back to composite
alone — graceful degradation.
"""

from __future__ import annotations

_DISAGREEMENT_THRESHOLD = 0.4


def fused_turn_score(composite_score: float, judge_score: float | None) -> float:
    """Final turn_score in [0, 1].

    composite-only when ``judge_score`` is None (cost guard, missing provider, parse
    failure). Otherwise the weighted combination (judge dominates).
    """
    if judge_score is None:
        return composite_score
    return 0.4 * composite_score + 0.6 * judge_score


def is_judge_disagreement(composite: float, judge: float | None) -> bool:
    """Flag turns where composite and judge diverge significantly.

    Surfaces signal-vs-LLM disagreement for review — may indicate weight
    mis-calibration or judge-prompt drift. False when the judge is absent.
    """
    if judge is None:
        return False
    return abs(composite - judge) > _DISAGREEMENT_THRESHOLD
