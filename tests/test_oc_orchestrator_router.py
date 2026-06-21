"""Tests for deterministic goal routing (orchestrator Stage-1 triage).

Proves a goal routes to the right specialized profile and shape, with a safe
fallback to single when nothing matches, and that route_and_assign creates a real
Kanban card assigned to the chosen profile. Real kanban + spine, no mocks.
"""

from __future__ import annotations

import pytest

from hermes_cli import kanban_db
from plugins.oc_orchestrator import router
from plugins.oc_runs import db as spine_db

PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]


def test_code_goal_routes_to_coder_single():
    d = router.route("Fix the failing unit test in the parser", available_profiles=PROFILES)
    assert d.profile == "coder" and d.shape == "single"


def test_research_goal_routes_to_atlas():
    d = router.route("Research and cite sources on the topic", available_profiles=PROFILES)
    assert d.profile == "atlas"


def test_finance_goal_routes_to_finance():
    d = router.route("Build a DCF valuation model from the 10-K", available_profiles=PROFILES)
    assert d.profile == "finance"


def test_no_match_falls_back_to_single_default():
    d = router.route("do the thing", available_profiles=PROFILES, default_profile="coder")
    assert d.shape == "single" and d.profile == "coder"
    assert "fallback" in d.rationale


def test_multi_domain_goal_fans_out_to_swarm():
    d = router.route("Research the market then build a financial model", available_profiles=PROFILES)
    assert d.shape == "swarm"
    assert "atlas" in d.candidates and "finance" in d.candidates


def test_unavailable_profile_is_not_chosen():
    # finance not available -> a finance goal must not route to finance.
    d = router.route("Build a DCF valuation model", available_profiles=["coder", "atlas"],
                     default_profile="coder")
    assert d.profile in ("coder", "atlas")


# --------------------------------------------------------------------------- #
# route_and_assign against the real Kanban DB
# --------------------------------------------------------------------------- #

def _reset_spine():
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            try:
                if attr == "conn" and spine_db._local.conn is not None:
                    spine_db._local.conn.close()
            except Exception:
                pass
            delattr(spine_db._local, attr)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset_spine()
    conn = kanban_db.connect(db_path=tmp_path / "kanban.db")
    yield conn
    try:
        conn.close()
    except Exception:
        pass
    _reset_spine()


def test_route_and_assign_creates_card_for_chosen_profile(env):
    conn = env
    decision, task_id = router.route_and_assign(
        conn, "Refactor and debug the API endpoint", available_profiles=PROFILES, board="test")
    assert decision.profile == "coder"
    task = kanban_db.get_task(conn, task_id)
    assert task is not None and task.assignee == "coder"
