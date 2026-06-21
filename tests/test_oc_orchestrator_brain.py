"""Tests for the advisory brain: LLM judgement with deterministic safe fallbacks.

Every entrypoint must (a) use a valid LLM answer, (b) reject/ignore a bad one and
fall back safely, and (c) never let an LLM output breach the candidate set or caps.
Uses a fake llm callable; no real model.
"""

from __future__ import annotations

import json

from plugins.oc_orchestrator import brain, caps

PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]


def _llm_returning(text):
    return lambda prompt: text


# 1. route_decompose --------------------------------------------------------- #

def test_route_decompose_single_goal_no_llm_needed():
    out = brain.route_decompose("Fix the failing unit test", PROFILES)
    assert out["shape"] == "single"
    assert out["lead"] == "coder"


def test_route_decompose_swarm_uses_llm_validated():
    goal = "Research the market then build a financial model"
    llm = _llm_returning(json.dumps([
        {"profile": "atlas", "subtask": "research the market"},
        {"profile": "finance", "subtask": "build the model"},
        {"profile": "HACKER", "subtask": "should be dropped"},  # not a candidate
    ]))
    out = brain.route_decompose(goal, PROFILES, llm=llm)
    assert out["shape"] == "swarm"
    profs = [s["profile"] for s in out["subtasks"]]
    assert "atlas" in profs and "finance" in profs
    assert "HACKER" not in profs  # validated against the candidate set


def test_route_decompose_caps_runaway_llm():
    goal = "Research the market then build a financial model"  # swarm
    huge = json.dumps([{"profile": "atlas", "subtask": f"t{i}"} for i in range(50)])
    out = brain.route_decompose(goal, PROFILES, llm=_llm_returning(huge), max_fanout=3)
    assert len(out["subtasks"]) <= 3  # hard cap holds regardless of llm


def test_route_decompose_brain_down_falls_back():
    goal = "Research the market then build a financial model"
    def boom(_): raise RuntimeError("model down")
    # decompose swallows a raising llm and falls back to deterministic slices.
    out = brain.route_decompose(goal, PROFILES, llm=boom)
    assert out["shape"] == "swarm"
    assert len(out["subtasks"]) >= 1  # deterministic per-profile fallback


# 2. classify_failure -------------------------------------------------------- #

def test_classify_failure_deterministic_cases_skip_llm():
    assert brain.classify_failure({"reason": "process_died"}, attempt_no=1) == "retry"
    assert brain.classify_failure({"reason": "security"}) == "escalate"
    assert brain.classify_failure({"reason": "same_signature_twice"}) == "reassign"
    assert brain.classify_failure({"reason": "timeout"}, attempt_no=99, max_attempts=3) == "escalate"


def test_classify_failure_opaque_uses_llm_then_validates():
    good = brain.classify_failure({"reason": "weird"}, llm=_llm_returning('{"action":"abort"}'))
    assert good == "abort"
    # garbage llm -> safe fallback
    bad = brain.classify_failure({"reason": "weird"}, llm=_llm_returning("lol no json"), attempt_no=1)
    assert bad == "retry"


# 3. need_verifier ----------------------------------------------------------- #

def test_need_verifier_code_always_gated():
    out = brain.need_verifier({"kind": "code"}, {}, llm=_llm_returning('{"verify": false}'))
    assert out["verify"] is True  # code ignores the llm; always gated


def test_need_verifier_noncode_uses_llm_else_flags_gap():
    yes = brain.need_verifier({"kind": "doc"}, {}, llm=_llm_returning('{"verify": true}'))
    assert yes["verify"] is True and yes["reviewer"] == "reviewer"
    fallback = brain.need_verifier({"kind": "doc"}, {}, llm=_llm_returning("nope"))
    assert fallback["verify"] is False and fallback.get("coverage_gap") is True


# 4. fanout_or_stop ---------------------------------------------------------- #

def test_fanout_refuses_out_of_bounds_and_defaults_safe():
    over = brain.fanout_or_stop({"requested": caps.HARD_CEILINGS["max_fanout"] + 5})
    assert over["approve"] is False
    no_llm = brain.fanout_or_stop({"requested": 2})  # no llm -> safe refuse
    assert no_llm["approve"] is False


def test_fanout_approves_only_on_valid_llm_yes():
    ok = brain.fanout_or_stop({"requested": 2}, llm=_llm_returning('{"approve": true}'))
    assert ok["approve"] is True
    garbage = brain.fanout_or_stop({"requested": 2}, llm=_llm_returning("maybe"))
    assert garbage["approve"] is False  # safe default
