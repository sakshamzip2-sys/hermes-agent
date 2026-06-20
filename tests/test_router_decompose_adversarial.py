"""Adversarial probes for orchestrator Stage-1 router and Stage-2 decompose.

These probes are hostile: each asserts the ROBUST behavior the module documents
(deterministic fallback, candidate-set validation, hard fan-out cap), so any
crash, missing sanitization, or runaway fan-out surfaces as a FAILED assertion.

No DB needed: route() and decompose() are pure functions over their args.
"""

from __future__ import annotations

import pytest

from plugins.oc_orchestrator import router
from plugins.oc_orchestrator import decompose as dc
from plugins.oc_orchestrator.caps import HARD_CEILINGS

PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]
HARD_FANOUT = HARD_CEILINGS["max_fanout"]


# --------------------------------------------------------------------------
# router.route hostile inputs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("goal", ["", "   ", "\t\n  ", None])
def test_route_empty_or_none_goal_falls_back_single_default(goal):
    # An empty/whitespace/None goal matches no keyword -> single + default.
    d = router.route(goal, available_profiles=PROFILES, default_profile="coder")
    assert d.shape == "single"
    assert d.profile == "coder"
    assert d.profile in PROFILES


def test_route_very_long_goal_does_not_crash():
    # A 200k-char goal must route deterministically without blowing up.
    goal = ("refactor the function and fix the bug " * 5000)
    d = router.route(goal, available_profiles=PROFILES)
    assert d.profile in PROFILES
    assert d.shape in ("single", "swarm")


def test_route_unicode_and_emoji_goal_does_not_crash():
    # Build the hostile string purely from escapes: CJK, emoji, RLO override,
    # and the U+FFFF noncharacter. No raw exotic bytes live in this source file.
    goal = (
        "重构这个函数 "  # CJK "refactor this function"
        "\U0001F600 fix the bug "                 # emoji + real keyword
        "‮ implement ￿ trailing"        # RLO override + noncharacter
    )
    d = router.route(goal, available_profiles=PROFILES)
    assert d.profile in PROFILES
    assert d.shape in ("single", "swarm")


def test_route_empty_available_profiles_does_not_crash():
    # No profiles available at all: must not raise IndexError; returns something.
    d = router.route("fix the bug", available_profiles=[])
    assert d.shape == "single"
    # With nothing available the documented fallback is the default_profile.
    assert d.profile == "coder"


def test_route_empty_avail_with_matching_keyword_does_not_crash():
    # A real keyword match but zero available profiles: matched filters to empty,
    # then the no-match branch must survive empty avail.
    d = router.route("research and cite sources", available_profiles=[])
    assert d.shape == "single"
    assert d.profile == "coder"


def test_route_default_not_in_available_returns_valid_available_profile():
    # default_profile not available + no keyword match: must pick an AVAILABLE one,
    # never hand back a profile the caller cannot spawn.
    avail = ["atlas", "sage"]
    d = router.route("hello there friend", available_profiles=avail, default_profile="coder")
    assert d.profile in avail, f"returned unavailable profile {d.profile!r}"
    assert d.shape == "single"


def test_route_swarm_candidates_are_all_available():
    # Multi-domain goal -> swarm; every candidate must be an available profile.
    goal = "research and cite sources, then implement and debug the fix"
    d = router.route(goal, available_profiles=PROFILES)
    assert d.shape == "swarm"
    assert d.candidates, "swarm decision must expose candidates"
    assert set(d.candidates).issubset(set(PROFILES))


def test_route_matched_profile_not_available_is_dropped():
    # Keyword matches 'coder' but coder is NOT available -> must not leak coder.
    d = router.route("fix the bug and debug the stack trace",
                     available_profiles=["atlas", "sage"], default_profile="atlas")
    assert d.profile in ("atlas", "sage")
    assert d.profile != "coder"


# --------------------------------------------------------------------------
# decompose hostile inputs
# --------------------------------------------------------------------------

def test_decompose_empty_candidates_returns_empty():
    assert dc.decompose("any goal", [], llm=None) == []
    assert dc.decompose("any goal", [], llm=lambda g, c: [{"profile": "coder", "subtask": "x"}]) == []


def test_decompose_fallback_one_per_candidate_capped():
    cands = ["coder", "atlas", "sage"]
    out = dc.decompose("ship the thing", cands, llm=None)
    assert len(out) == 3
    assert [s.profile for s in out] == cands


def test_decompose_fallback_respects_max_fanout():
    cands = ["coder", "atlas", "sage", "ledger", "finance"]
    out = dc.decompose("goal", cands, llm=None, max_fanout=2)
    assert len(out) == 2


def test_decompose_llm_duplicates_are_not_capped_silently_into_runaway():
    # An llm that returns the same profile many times: every dup is in-candidate,
    # so output is bounded by the cap, never exploding past it.
    cands = ["coder"]

    def llm(goal, candidates):
        return [{"profile": "coder", "subtask": f"part {i}"} for i in range(1000)]

    out = dc.decompose("g", cands, llm=llm)
    assert len(out) <= HARD_FANOUT, f"runaway fan-out: {len(out)} > {HARD_FANOUT}"


def test_decompose_llm_out_of_candidate_profiles_dropped():
    cands = ["coder", "atlas"]

    def llm(goal, candidates):
        return [
            {"profile": "coder", "subtask": "ok"},
            {"profile": "ATTACKER", "subtask": "evil"},
            {"profile": "finance", "subtask": "not a candidate"},
            {"profile": "atlas", "subtask": "ok2"},
        ]

    out = dc.decompose("g", cands, llm=llm)
    assert {s.profile for s in out} == {"coder", "atlas"}
    assert all(s.profile in cands for s in out)


def test_decompose_llm_1000_entries_hard_capped():
    cands = ["coder", "atlas", "sage"]

    def llm(goal, candidates):
        # cycle through valid candidates so none are dropped for being invalid
        return [{"profile": cands[i % 3], "subtask": f"s{i}"} for i in range(1000)]

    out = dc.decompose("g", cands, llm=llm)
    assert len(out) <= HARD_FANOUT, f"hard cap breached: {len(out)}"


def test_decompose_llm_malformed_dicts_do_not_crash():
    cands = ["coder", "atlas"]

    def llm(goal, candidates):
        return [
            {},                                   # missing profile + subtask
            {"profile": "coder"},                 # missing subtask -> fallback text
            {"subtask": "orphan"},                # missing profile -> dropped
            {"profile": "atlas", "subtask": ""},  # empty subtask -> fallback text
            None,                                 # entry is None
        ]

    out = dc.decompose("the goal", cands, llm=llm)
    # Only coder and atlas have a valid in-candidate profile.
    assert {s.profile for s in out} == {"coder", "atlas"}
    # No subtask text may be empty/None: the fallback must fill it.
    for s in out:
        assert s.subtask, f"empty subtask leaked for profile {s.profile!r}"


def test_decompose_llm_returns_none_treated_as_empty():
    out = dc.decompose("g", ["coder"], llm=lambda goal, c: None)
    assert out == []


def test_decompose_negative_max_fanout_clamped_to_zero():
    out = dc.decompose("g", ["coder", "atlas"], llm=None, max_fanout=-5)
    assert out == []


def test_decompose_llm_non_dict_entries_do_not_crash():
    # An llm that yields strings/ints instead of dicts must be tolerated, not crash.
    cands = ["coder"]

    def llm(goal, candidates):
        return ["just a string", 42, ("tuple",), {"profile": "coder", "subtask": "valid"}]

    out = dc.decompose("g", cands, llm=llm)
    # The one valid entry should survive; the garbage must be skipped, not raise.
    assert all(s.profile == "coder" for s in out)
    assert any(s.subtask == "valid" for s in out)
