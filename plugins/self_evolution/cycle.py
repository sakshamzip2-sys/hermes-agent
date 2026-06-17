"""The unified self-evolution cycle — fires the whole flywheel as ONE flow.

This is the piece that actually CLOSES the loop in running code. It sequences the organs
in dependency order, each step fail-soft (one down step never kills the cycle):

    1. SENSE     outcomes.run_cycle()            — roll up turn_scores (+ batch judge)
    2. DREAM     dreaming.run_dream_cycle()      — consolidate (outcome-tuned), get promoted facts
    3. ENRICH    dream_orchestrator.run_all()    — cross-engine (Honcho/GBrain), if present
    4. EVOLVE    playbook.synthesize_from_facts  — turn recurring patterns into skills

Every step is resolved lazily + guarded, so the cycle runs even when a plugin is absent or
a backend is down. ``plan=True`` reports what WOULD run without side effects. The step
callables are injectable so the orchestration (sequencing, fail-soft, fact hand-off) is
unit-testable with no real LLM / state.db / network.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("hermes.plugins.self_evolution.cycle")


async def _maybe_await(result):
    """Await ``result`` if it's a coroutine/awaitable, else return it as-is.

    Lets the orchestration accept both sync and async step callables (real steps mix
    both; injected test steps may be either)."""
    import inspect

    if inspect.isawaitable(result):
        return await result
    return result


# --- default step implementations (each lazily resolved + fail-soft) ---------
def _default_outcomes_step() -> dict:
    try:
        from plugins.outcomes import run_cycle

        return {"ok": True, "data": run_cycle()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _default_dream_step(*, force: bool) -> dict:
    """Returns {ok, counts, promoted_facts}. promoted_facts feeds the EVOLVE step."""
    try:
        from plugins.dreaming.runner import run_dream_cycle

        summary = await run_dream_cycle(force=force)
        facts = [r.candidate.raw_text for r in getattr(summary, "promoted", ())]
        facts += [r.candidate.raw_text for r in getattr(summary, "updated", ())]
        return {"ok": True, "counts": summary.counts(), "promoted_facts": facts}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "promoted_facts": []}


async def _default_cross_engine_step(*, force: bool) -> dict:
    """Run the cross-engine orchestrator (Honcho/GBrain) if the plugin is present.

    ``dream_orchestrator.run_all`` is SYNC but internally uses ``asyncio.run``; calling it
    directly from our async cycle would nest event loops (RuntimeError + never-awaited
    coroutines). Running it on a worker thread gives it a clean loop-free context.
    """
    import asyncio

    try:
        from plugins.dream_orchestrator import run_all
    except ImportError:
        return {"ok": True, "skipped": "dream_orchestrator not installed"}
    try:
        data = await asyncio.to_thread(run_all, force=force)
        return {"ok": True, "data": data}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _default_playbook_step(facts: list) -> dict:
    try:
        from plugins.playbook_synthesizer import synthesize_from_facts

        return {"ok": True, "data": await synthesize_from_facts(facts)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


_DEFAULT_STEPS = {
    "outcomes": _default_outcomes_step,
    "dream": _default_dream_step,
    "cross_engine": _default_cross_engine_step,
    "playbook": _default_playbook_step,
}


async def run_cycle(*, force: bool = False, plan: bool = False, steps: Optional[dict] = None) -> dict:
    """Run (or plan) one full self-evolution cycle. Returns a combined summary."""
    s = {**_DEFAULT_STEPS, **(steps or {})}
    summary: dict = {"plan": plan, "force": force, "steps": {}}

    if plan:
        summary["steps"] = {
            "outcomes": "would roll up turn_scores (+ batch judge if enabled)",
            "dream": "would run a consolidation pass (outcome-tuned)",
            "cross_engine": "would run Honcho/GBrain orchestration (if present)",
            "playbook": "would synthesize skills from promoted facts (if enabled)",
        }
        return summary

    # 1. SENSE
    summary["steps"]["outcomes"] = await _maybe_await(s["outcomes"]())
    # 2. DREAM (produces the facts the EVOLVE step consumes)
    dream = await _maybe_await(s["dream"](force=force))
    summary["steps"]["dream"] = dream
    promoted_facts = dream.get("promoted_facts", []) if isinstance(dream, dict) else []
    # 3. ENRICH (cross-engine)
    summary["steps"]["cross_engine"] = await _maybe_await(s["cross_engine"](force=force))
    # 4. EVOLVE (turn recurring promoted patterns into skills)
    summary["steps"]["playbook"] = await _maybe_await(s["playbook"](promoted_facts))

    summary["promoted_fact_count"] = len(promoted_facts)
    summary["ok"] = all(
        st.get("ok", True) for st in summary["steps"].values() if isinstance(st, dict)
    )
    return summary


def render(summary: dict) -> str:
    """Human-readable one-screen rendering of a cycle summary."""
    if summary.get("plan"):
        lines = ["Self-evolution cycle — PLAN (dry-run):"]
        for name, what in summary.get("steps", {}).items():
            lines.append(f"  · {name:13s} {what}")
        return "\n".join(lines)

    lines = [f"Self-evolution cycle {'✓' if summary.get('ok') else '✗ (a step failed)'}"]
    for name, st in summary.get("steps", {}).items():
        if not isinstance(st, dict):
            lines.append(f"  {name}: {st}")
            continue
        if st.get("ok"):
            detail = st.get("skipped") or _short(st.get("data") or st.get("counts") or "ok")
            lines.append(f"  ✓ {name:13s} {detail}")
        else:
            lines.append(f"  ✗ {name:13s} {st.get('error', 'failed')}")
    lines.append(f"  promoted facts → EVOLVE: {summary.get('promoted_fact_count', 0)}")
    return "\n".join(lines)


def _short(obj) -> str:
    txt = str(obj)
    return txt if len(txt) <= 160 else txt[:157] + "..."
