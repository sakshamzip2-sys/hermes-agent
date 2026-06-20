"""Tests for the Stage-2 decompose seam (swarm goal -> per-profile subtasks).

Proves the deterministic fallback (no model) emits one subtask per candidate
profile, that an injected llm's output is validated (out-of-candidates profiles
dropped) and capped at max_fanout so runaway fan-out is impossible regardless of
what the llm returns, and that empty candidates yield no subtasks. Model-agnostic:
the llm is an injected fake callable, never a real provider.
"""

from __future__ import annotations

from plugins.oc_orchestrator import decompose
from plugins.oc_orchestrator.caps import HARD_CEILINGS

CANDIDATES = ["coder", "atlas", "finance"]


def test_fallback_one_subtask_per_candidate():
    goal = "Research the market then build a model and ship the code"
    subs = decompose.decompose(goal, CANDIDATES)
    assert [s.profile for s in subs] == CANDIDATES
    assert len(subs) == len(CANDIDATES)
    for s in subs:
        # Each subtask must be a real per-profile slice of the goal, not empty.
        assert s.profile in s.subtask
        assert goal in s.subtask


def test_fallback_caps_at_max_fanout():
    many = [f"profile_{i}" for i in range(HARD_CEILINGS["max_fanout"] + 20)]
    subs = decompose.decompose("do the big thing", many)
    assert len(subs) == HARD_CEILINGS["max_fanout"]


def test_fallback_respects_explicit_max_fanout():
    subs = decompose.decompose("do it", CANDIDATES, max_fanout=2)
    assert len(subs) == 2
    assert [s.profile for s in subs] == CANDIDATES[:2]


def test_llm_output_used_when_provided():
    def fake_llm(goal, candidates):
        return [
            {"profile": "coder", "subtask": "write the parser"},
            {"profile": "atlas", "subtask": "gather sources"},
        ]

    subs = decompose.decompose("goal", CANDIDATES, llm=fake_llm)
    assert [(s.profile, s.subtask) for s in subs] == [
        ("coder", "write the parser"),
        ("atlas", "gather sources"),
    ]


def test_llm_out_of_candidates_profile_is_dropped():
    def fake_llm(goal, candidates):
        return [
            {"profile": "coder", "subtask": "write code"},
            {"profile": "ledger", "subtask": "not a candidate -> drop me"},
            {"profile": "atlas", "subtask": "research"},
        ]

    subs = decompose.decompose("goal", CANDIDATES, llm=fake_llm)
    profiles = [s.profile for s in subs]
    assert "ledger" not in profiles
    assert profiles == ["coder", "atlas"]


def test_llm_runaway_fanout_is_capped():
    def runaway_llm(goal, candidates):
        # 50 entries, all valid candidates -> still must be capped.
        return [{"profile": "coder", "subtask": f"slice {i}"} for i in range(50)]

    subs = decompose.decompose("goal", CANDIDATES, llm=runaway_llm)
    assert len(subs) == HARD_CEILINGS["max_fanout"]


def test_empty_candidates_returns_empty_list():
    assert decompose.decompose("anything", []) == []


def test_empty_candidates_with_llm_returns_empty_list():
    def fake_llm(goal, candidates):
        return [{"profile": "coder", "subtask": "x"}]

    # No candidates means nothing is a valid profile -> everything dropped.
    assert decompose.decompose("anything", [], llm=fake_llm) == []


def test_subtask_is_dataclass_with_rationale():
    subs = decompose.decompose("solve it", ["coder"])
    s = subs[0]
    assert s.profile == "coder"
    assert isinstance(s.rationale, str) and s.rationale
