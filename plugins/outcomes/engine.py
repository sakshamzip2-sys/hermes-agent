"""Outcomes engine — orchestrate signals → composite → judge → fusion → store.

The runtime glue of the SENSE organ. Hooks feed it (``record_tool`` from
``post_tool_call``; ``finalize_turn`` at turn end / next-user-message), it computes the
fused ``turn_score`` and persists it. Pure-ish + injectable (store + judge_fn) so it is
unit-testable with no network.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Callable, Optional

from .composite import compute_composite_score
from .fusion import fused_turn_score
from .signals import TurnSignals

logger = logging.getLogger("hermes.plugins.outcomes.engine")

# A judge callable: (*, trajectory_summary, composite_score, standing_orders) -> float | None
JudgeFn = Callable[..., Optional[float]]

# Cap on tracked live/pending sessions so a long-running gateway can't leak memory via
# abandoned sessions (one that never reaches on_session_end). Oldest are evicted (LRU).
DEFAULT_MAX_SESSIONS = 1024


class OutcomesEngine:
    """Per-session signal accumulation + per-turn fused scoring into the store.

    NOT inherently thread-safe at the data level, but the engine is a process-wide
    singleton shared across gateway sessions, so mutating ops take a coarse RLock. The
    live/pending session maps are LRU-bounded (``max_sessions``) so abandoned sessions
    cannot grow memory without bound.
    """

    def __init__(self, store, *, judge_enabled: bool = False) -> None:  # noqa: ANN001
        self.store = store
        self.judge_enabled = judge_enabled
        self.max_sessions = DEFAULT_MAX_SESSIONS
        self._signals: "OrderedDict[str, TurnSignals]" = OrderedDict()
        # session_id -> (turn, signals_snapshot, trajectory_summary) for the delayed model
        self._pending: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _evict(od: "OrderedDict", cap: int) -> None:
        while len(od) > max(1, cap):
            od.popitem(last=False)  # drop the oldest (LRU)

    def signals_for(self, session_id: str) -> TurnSignals:
        with self._lock:
            sig = self._signals.get(session_id)
            if sig is None:
                sig = TurnSignals()
                self._signals[session_id] = sig
                self._evict(self._signals, self.max_sessions)
            else:
                self._signals.move_to_end(session_id)
            return sig

    def record_tool(self, session_id: str, *, success: bool) -> None:
        """``post_tool_call`` feeds this — accumulate tool outcome for the live turn."""
        with self._lock:
            self.signals_for(session_id).record_tool(success=success)

    def finalize_turn(
        self,
        session_id: str,
        turn: int,
        *,
        trajectory_summary: str = "",
        standing_orders: str = "",
        user_followup: Optional[str] = None,
        judge_fn: Optional[JudgeFn] = None,
        now: Optional[float] = None,
    ) -> float:
        """Score the just-finished turn, persist it, and reset the session's signals.

        Immediate model (manual/CLI/tests): scores the live signals now. The hook path
        uses the one-turn-delayed :meth:`stage_turn` / :meth:`resolve_pending` instead, so
        the user's correction (which arrives on the *next* message) lands on the right turn.

        ``judge_fn`` (optional, injected) returns a judge score in [0,1] or None; it is
        only consulted when ``judge_enabled`` is True. Returns the fused turn_score.
        """
        sig = self.signals_for(session_id)
        self._signals.pop(session_id, None)
        return self._score_and_record(
            session_id, turn, sig,
            trajectory_summary=trajectory_summary, standing_orders=standing_orders,
            user_followup=user_followup, judge_fn=judge_fn, now=now,
        )

    # -- one-turn-delayed model (the hook path) ----------------------------------
    def stage_turn(self, session_id: str, turn, *, trajectory_summary: str = "") -> None:  # noqa: ANN001
        """Snapshot the live turn's signals as 'pending', awaiting next-message feedback.

        Resets live signals so the next turn accumulates fresh. If a prior turn is still
        pending (no feedback arrived), it is flushed first so nothing is lost.
        """
        with self._lock:
            if session_id in self._pending:
                # No feedback came for the prior staged turn; flush it as-is.
                self.flush_pending(session_id)
            sig = self.signals_for(session_id)
            self._signals.pop(session_id, None)
            self._pending[session_id] = (turn, sig, trajectory_summary)
            self._evict(self._pending, self.max_sessions)

    def resolve_pending(
        self, session_id: str, *,
        user_followup: Optional[str] = None,
        standing_orders: str = "",
        judge_fn: Optional[JudgeFn] = None,
        now: Optional[float] = None,
    ) -> Optional[float]:
        """Score the pending turn using ``user_followup`` (its feedback). None if none pending."""
        with self._lock:
            pend = self._pending.pop(session_id, None)
        if pend is None:
            return None
        turn, sig, trajectory = pend
        return self._score_and_record(
            session_id, turn, sig,
            trajectory_summary=trajectory, standing_orders=standing_orders,
            user_followup=user_followup, judge_fn=judge_fn, now=now,
        )

    def flush_pending(
        self, session_id: str, *,
        judge_fn: Optional[JudgeFn] = None,
        now: Optional[float] = None,
    ) -> Optional[float]:
        """Score the pending turn with no feedback (session end). None if none pending."""
        return self.resolve_pending(session_id, user_followup=None, judge_fn=judge_fn, now=now)

    def _score_and_record(
        self,
        session_id: str,
        turn,  # noqa: ANN001
        sig: TurnSignals,
        *,
        trajectory_summary: str = "",
        standing_orders: str = "",
        user_followup: Optional[str] = None,
        judge_fn: Optional[JudgeFn] = None,
        now: Optional[float] = None,
    ) -> float:
        if user_followup:
            sig.apply_user_followup(user_followup)

        composite = compute_composite_score(**sig.to_score_kwargs())

        judge_score: Optional[float] = None
        if self.judge_enabled and judge_fn is not None:
            try:
                raw = judge_fn(
                    trajectory_summary=trajectory_summary or _auto_trajectory(sig),
                    composite_score=composite,
                    standing_orders=standing_orders,
                )
                judge_score = None if raw is None else max(0.0, min(1.0, float(raw)))
            except Exception as exc:  # noqa: BLE001 — judge must never break the loop
                logger.debug("outcomes: judge_fn failed (%s); composite-only", exc)
                judge_score = None

        fused = fused_turn_score(composite, judge_score)
        self.store.record(
            session_id=session_id,
            turn=turn,  # str|int — the real hook turn_id is a non-numeric string
            turn_score=fused,
            composite=composite,
            judge=judge_score,
            trajectory=trajectory_summary or _auto_trajectory(sig),
            ts=float(now if now is not None else time.time()),
        )
        # Reset this session's signals for the next turn.
        self._signals.pop(session_id, None)
        return fused

    def run_cycle(self) -> dict:
        """Nightly-deployment entrypoint: emit a summary of recent outcomes.

        Scoring is per-turn (hook-driven), so the cycle is a lightweight rollup the
        cross-engine plane and dreaming can read — no heavy work here.
        """
        recent = self.store.recent_turn_scores(limit=150)
        mean_recent = sum(recent) / len(recent) if recent else None
        return {
            "recorded": self.store.count(),
            "recent_n": len(recent),
            "mean_recent": mean_recent,
        }

    async def rejudge_recent(
        self, *,
        limit: int = 150,
        chat_fn=None,  # noqa: ANN001
        standing_orders: str = "",
        now: Optional[float] = None,
    ) -> int:
        """Batch-apply the aux-LLM judge to recently-recorded composite-only turns.

        Per-turn scoring stays composite-only (free, synchronous, hot-path-safe). The
        judge — which is async and costs tokens — runs HERE, in the consolidation cycle,
        re-scoring un-judged turns and fusing the verdict in. This is naturally
        cost-bounded (once per cycle, not per turn) and matches Anthropic's batch
        Outcomes model. No-op when ``judge_enabled`` is False. Returns #turns re-judged.
        """
        if not self.judge_enabled:
            return 0
        from .judge import score_turn_via_judge

        rows = self.store.recent_unjudged_rows(limit=limit)
        rejudged = 0
        for row in rows:
            composite = row.get("composite")
            if composite is None:
                composite = row.get("turn_score", 0.5)
            verdict = await score_turn_via_judge(
                trajectory_summary=row.get("trajectory", "") or "",
                composite_score=float(composite),
                standing_orders=standing_orders,
                chat_fn=chat_fn,
            )
            if verdict is None:
                continue
            fused = fused_turn_score(float(composite), verdict.judge_score)
            self.store.update_judged(row["id"], judge=verdict.judge_score, turn_score=fused)
            rejudged += 1
        return rejudged


def _auto_trajectory(sig: TurnSignals) -> str:
    """A terse, deterministic trajectory summary when the host doesn't supply one."""
    return (
        f"tools: {sig.tool_success_count} ok / {sig.tool_error_count} err; "
        f"retries={sig.retry_count}; self_cancel={sig.self_cancel_count}"
    )
