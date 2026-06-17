"""Outcome-driven threshold modulator — pure, bounded, conservative.

Ported from OpenComputer v1 (``evolution/dreaming_outcomes.py``). Turns the recent
``turn_score`` history into a small delta applied on top of dreaming's
``score_threshold``:

* recent window clearly ABOVE baseline → relax (lower the bar; outcomes are good);
* N consecutive windows BELOW baseline-margin → tighten (raise the bar; be stricter
  about what graduates to memory).

The delta is clamped to ``[-max_delta, +max_delta]`` (default ±0.05) so outcome tuning
never swings hard around the dreaming baseline. This is the OUTCOMES → DREAM connection
in the self-evolution loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class OutcomeAdjustmentConfig:
    """Tunables for the outcome-driven threshold modulator."""

    window_size: int = 50
    n_windows: int = 3
    margin: float = 0.05
    min_low_windows: int = 2
    up_step: float = 0.05
    down_step: float = 0.05
    max_delta: float = 0.05


def compute_outcome_adjustment(
    turn_scores: Sequence[float],
    *,
    baseline_threshold: float,
    config: OutcomeAdjustmentConfig | None = None,
) -> float:
    """Compute a delta to apply on top of ``baseline_threshold``.

    Positive = stricter (raise the bar); negative = more permissive (lower it).
    Always within ``[-max_delta, +max_delta]``. ``turn_scores`` is newest-first;
    empty input → 0.0.
    """
    cfg = config or OutcomeAdjustmentConfig()
    if not turn_scores:
        return 0.0

    # Slice into n_windows windows of window_size each, newest first.
    windows: list[list[float]] = []
    for i in range(cfg.n_windows):
        start = i * cfg.window_size
        end = start + cfg.window_size
        chunk = list(turn_scores[start:end])
        if not chunk:
            break
        windows.append(chunk)
    if not windows:
        return 0.0

    window_means = [mean(w) for w in windows]
    most_recent = window_means[0]

    # Hot path: recent window clearly above baseline → relax.
    if most_recent > baseline_threshold:
        return max(-cfg.max_delta, min(cfg.max_delta, -cfg.down_step))

    # Stricter path: N consecutive low windows (from newest) → raise.
    low_threshold = baseline_threshold - cfg.margin
    consecutive_low = 0
    for m in window_means:
        if m < low_threshold:
            consecutive_low += 1
        else:
            break
    if consecutive_low >= cfg.min_low_windows:
        return max(-cfg.max_delta, min(cfg.max_delta, cfg.up_step))

    return 0.0


def fetch_recent_turn_scores_from_db(db_path, *, limit: int = 150) -> list[float]:
    """Read the most recent ``turn_outcomes.turn_score`` values, newest-first.

    Returns an empty list on any read error so the dreaming tick degrades gracefully
    to no adjustment. (Thin sqlite reader; the outcomes store owns the schema.)
    """
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute(
                "SELECT turn_score FROM turn_outcomes "
                "WHERE turn_score IS NOT NULL "
                "ORDER BY rowid DESC LIMIT ?",
                (int(limit),),
            )
            return [float(row[0]) for row in cur.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error:
        return []
