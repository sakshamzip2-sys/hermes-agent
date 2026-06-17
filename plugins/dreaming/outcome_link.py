"""SENSE → DREAM link: let recent turn-outcomes tune the dreaming promotion bar.

This is the connective tissue that turns two separate plugins into one loop. The
``outcomes`` plugin records a per-turn ``turn_score``; here the dreaming runner reads the
recent window and nudges its ``score_threshold``:

* outcomes recently GOOD  → relax the bar (the agent is doing well; let more in);
* outcomes recently POOR  → tighten the bar (be stricter about what graduates to memory).

The adjustment is the bounded dead-band modulator ported from v1 (``outcomes.tuner``), so
the swing is small (±0.05). Fail-soft: if the outcomes plugin is absent or its store is
empty, the base threshold is returned unchanged — dreaming keeps working standalone.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("hermes.plugins.dreaming.outcome_link")

# A scores provider returns recent turn_scores, newest-first. Injectable for tests.
ScoresProvider = Callable[[], list]

# Don't adjust on too little history — a couple of turns is statistical noise. (Applied
# at the integration point so the ported tuner stays byte-faithful to v1.)
_MIN_HISTORY = 10


def adjusted_score_threshold(
    base_threshold: float,
    *,
    scores_provider: Optional[ScoresProvider] = None,
) -> float:
    """Return ``base_threshold`` nudged by recent turn-outcomes, clamped to [0, 1].

    Returns the base unchanged when outcomes are unavailable, the history is too short,
    or anything fails.
    """
    try:
        scores = list(scores_provider() if scores_provider is not None else _default_scores())
    except Exception as exc:  # noqa: BLE001 — never let the link break dreaming
        logger.debug("dreaming.outcome_link: scores unavailable (%s)", exc)
        return base_threshold

    if len(scores) < _MIN_HISTORY:
        return base_threshold

    try:
        from plugins.outcomes.tuner import compute_outcome_adjustment

        delta = compute_outcome_adjustment(scores, baseline_threshold=base_threshold)
    except Exception as exc:  # noqa: BLE001
        logger.debug("dreaming.outcome_link: tuner unavailable (%s)", exc)
        return base_threshold

    adjusted = base_threshold + delta
    if delta:
        logger.info(
            "dreaming: outcome-tuned score_threshold %.3f -> %.3f (delta %+.3f, n=%d)",
            base_threshold, max(0.0, min(1.0, adjusted)), delta, len(scores),
        )
    return max(0.0, min(1.0, adjusted))


def _default_scores() -> list:
    """Read recent turn_scores from the outcomes plugin's store (fail-soft → [])."""
    try:
        from plugins.outcomes.store import recent_turn_scores

        return recent_turn_scores(limit=150)
    except Exception:  # noqa: BLE001 — outcomes plugin not present / not enabled
        return []
