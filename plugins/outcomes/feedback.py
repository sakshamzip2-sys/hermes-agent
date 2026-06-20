"""Reward signals from explicit user feedback (Part 2, Slice 5 / P2-6c).

Primitive reinforcement, deliberately small: a user action on a turn's output is
mapped to a bounded signal in [0, 1] and folded into a running mean. There is NO
model training here. This module never imports, calls, or schedules any LLM, SFT,
DPO, or RLHF path; it only writes a scalar average into two existing stores.

The two sinks (both reuse code that already exists):
  (a) the skill-usage sidecar's ``user_rating`` rolling mean, via
      ``tools.skill_usage.record_skill_outcome(skill, user_rating=signal)`` —
      a sample-count-weighted running mean (``tools/skill_usage.py``);
  (b) optionally the matching ``turn_outcomes`` row's ``user_rating`` running
      mean, via ``OutcomesStore.record_user_rating`` (additive nullable column,
      PRAGMA-guarded; a no-op on an un-migrated DB).

Action vocabulary (the only mapping; everything else is an unknown no-op):
  positive (signal 1.0): ``copied``, ``shared``, ``kept``
  negative (signal 0.0): ``heavy_edit``, ``regenerate``, ``discarded``

Positive actions raise the running mean; negative actions lower it. Every write is
best-effort and fail-soft: a broken store never raises into the caller, mirroring
the other outcome/usage bumps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.plugins.outcomes.feedback")

# The bounded reward range. Signals are clamped into [POSITIVE? , ...]; concretely
# every mapped action lands on one of these two poles, but the conversion is kept
# explicit so a future action could map to an intermediate value without changing
# call sites.
_SIGNAL_MIN = 0.0
_SIGNAL_MAX = 1.0

# action -> bounded signal in [0, 1]. This is the WHOLE controlled vocabulary; an
# action not present here is an explicit, safe no-op (no write to either store).
_ACTION_SIGNALS = {
    # positive: the user accepted/used the output as-is.
    "copied": _SIGNAL_MAX,
    "shared": _SIGNAL_MAX,
    "kept": _SIGNAL_MAX,
    # negative: the user rejected/heavily reworked the output.
    "heavy_edit": _SIGNAL_MIN,
    "regenerate": _SIGNAL_MIN,
    "discarded": _SIGNAL_MIN,
}

# Public, read-only views of the vocabulary for callers/tests that want to assert
# the mapping without reaching into the private dict.
POSITIVE_ACTIONS = frozenset(a for a, s in _ACTION_SIGNALS.items() if s >= 0.5)
NEGATIVE_ACTIONS = frozenset(a for a, s in _ACTION_SIGNALS.items() if s < 0.5)
KNOWN_ACTIONS = frozenset(_ACTION_SIGNALS)


def action_to_signal(action: str) -> Optional[float]:
    """Map a feedback *action* to a bounded reward signal in [0, 1].

    Returns the signal for a known action (case- and whitespace-insensitive), or
    ``None`` for an unknown action so callers can treat it as a safe no-op. Pure
    function: no I/O, no model call.
    """
    if not action or not isinstance(action, str):
        return None
    signal = _ACTION_SIGNALS.get(action.strip().lower())
    if signal is None:
        return None
    # Defensive clamp — the vocabulary is already in-range, but keep the bound
    # explicit so the contract ([0, 1]) holds even if the table is edited.
    return max(_SIGNAL_MIN, min(_SIGNAL_MAX, float(signal)))


def record_feedback(
    action: str,
    *,
    skill: Optional[str] = None,
    turn: Optional[str] = None,
    session_id: Optional[str] = None,
    db_path: Optional[Path | str] = None,
) -> Optional[float]:
    """Convert a user *action* to a reward signal and fold it into the stores.

    ``action`` is one of the ``KNOWN_ACTIONS`` (copied/shared/kept -> positive,
    heavy_edit/regenerate/discarded -> negative). Unknown actions are a safe no-op
    and return ``None`` without touching any store.

    Sinks (both reuse existing running-mean code, no new scorer, no model):
      - when ``skill`` is given, the signal feeds the skill sidecar via
        ``record_skill_outcome(skill, user_rating=signal)`` (running mean);
      - when both ``session_id`` and ``turn`` are given AND the optional
        ``turn_outcomes.user_rating`` column exists, the signal also folds into
        that turn's running mean (PRAGMA-guarded; no-op otherwise).

    Returns the bounded signal that was applied, or ``None`` for an unknown action.
    Every store write is best-effort and fail-soft (logged at DEBUG, never raised).
    """
    signal = action_to_signal(action)
    if signal is None:
        # Unknown action: explicit, total no-op. No write, no model, nothing.
        logger.debug("record_feedback: ignoring unknown action %r", action)
        return None

    # Sink (a): skill sidecar running mean. Reuses the existing helper, which is
    # itself atomic-write + flock and fail-soft.
    if skill:
        try:
            from tools.skill_usage import record_skill_outcome

            record_skill_outcome(skill, user_rating=signal)
        except Exception as exc:  # noqa: BLE001 — best-effort, never break the caller
            logger.debug("record_feedback: skill sidecar write failed (%s)", exc)

    # Sink (b): optional matching turn_outcomes row. Only when we can address a turn.
    if session_id and turn:
        try:
            from plugins.outcomes.store import OutcomesStore, default_db_path

            path = Path(db_path) if db_path is not None else default_db_path()
            # Read-or-create is fine: the store only opens an existing or new DB; it
            # never deletes. A missing turn row is a benign False (no-op).
            OutcomesStore(path).record_user_rating(
                session_id=session_id, turn=turn, signal=signal
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never break the caller
            logger.debug("record_feedback: turn_outcomes write failed (%s)", exc)

    return signal
