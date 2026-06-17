"""Tests for the unified self-evolution cycle orchestration (injected fake steps)."""

from __future__ import annotations

import asyncio

from plugins.self_evolution import cycle


def _run(coro):
    return asyncio.run(coro)


def _steps(order, *, dream_facts=("fact A",), fail=None):
    fail = fail or set()

    def outcomes():
        order.append("outcomes")
        if "outcomes" in fail:
            raise RuntimeError("outcomes boom")
        return {"ok": True, "data": {"recorded": 3}}

    async def dream(*, force):
        order.append("dream")
        return {"ok": True, "counts": {"promoted": len(dream_facts)},
                "promoted_facts": list(dream_facts)}

    def cross_engine(*, force):
        order.append("cross_engine")
        return {"ok": True, "data": {"targets": []}}

    async def feed_up():
        order.append("feed_up")
        return {"ok": True, "skipped": "test"}

    async def playbook(facts):
        order.append(("playbook", tuple(facts)))
        return {"ok": True, "data": {"created": list(facts)}}

    # Wrap outcomes to be fail-soft at the step boundary like the real default.
    def outcomes_safe():
        try:
            return outcomes()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    return {"outcomes": outcomes_safe, "dream": dream, "cross_engine": cross_engine,
            "feed_up": feed_up, "playbook": playbook}


def test_steps_run_in_dependency_order() -> None:
    order = []
    _run(cycle.run_cycle(steps=_steps(order)))
    names = [o if isinstance(o, str) else o[0] for o in order]
    assert names == ["outcomes", "dream", "cross_engine", "feed_up", "playbook"]


def test_dream_facts_flow_into_playbook() -> None:
    order = []
    _run(cycle.run_cycle(steps=_steps(order, dream_facts=("learned X", "learned Y"))))
    playbook_call = next(o for o in order if isinstance(o, tuple) and o[0] == "playbook")
    assert playbook_call[1] == ("learned X", "learned Y")


def test_cycle_is_fail_soft_per_step() -> None:
    order = []
    summary = _run(cycle.run_cycle(steps=_steps(order, fail={"outcomes"})))
    # outcomes failed but the rest still ran.
    assert summary["steps"]["outcomes"]["ok"] is False
    assert summary["steps"]["dream"]["ok"] is True
    assert summary["steps"]["playbook"]["ok"] is True
    assert summary["ok"] is False  # overall flagged failed


def test_plan_mode_runs_nothing() -> None:
    order = []
    summary = _run(cycle.run_cycle(plan=True, steps=_steps(order)))
    assert order == []  # no step executed
    assert summary["plan"] is True
    assert "outcomes" in summary["steps"]


def test_render_plan_and_run() -> None:
    order = []
    plan = _run(cycle.run_cycle(plan=True, steps=_steps(order)))
    assert "PLAN" in cycle.render(plan)
    run = _run(cycle.run_cycle(steps=_steps(order)))
    out = cycle.render(run)
    assert "outcomes" in out and "playbook" in out


def test_promoted_fact_count_reported() -> None:
    order = []
    summary = _run(cycle.run_cycle(steps=_steps(order, dream_facts=("a", "b", "c"))))
    assert summary["promoted_fact_count"] == 3
