"""Kanban coordination: cards assigned to profiles, projected into the run view.

Proves the Hermes-native coordination pattern end to end against the REAL Kanban
DB: the orchestrator assigns a card to a profile, and every card projects onto the
spine as a run parented to profiles:<assignee> with the Kanban status mapped into
the normalized vocabulary, so the parallel view shows cards under their profiles.
Real SQLite (kanban + spine), no mocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli import kanban_db
from plugins.oc_orchestrator import kanban_bridge
from plugins.oc_runs import db as spine_db
from plugins.parallel_view import projection


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


def _views():
    return {v["run_id"]: v for v in projection.build_view_from_spine()}


def test_assign_card_creates_card_assigned_to_profile(env):
    conn = env
    tid = kanban_bridge.assign_card(conn, title="Fix the parser", profile="coder", board="test")
    # The real Kanban card exists and is assigned to the coder profile.
    task = kanban_db.get_task(conn, tid)
    assert task is not None
    assert task.assignee == "coder"
    # And it shows on the spine parented to the coder profile.
    v = _views()[kanban_bridge.card_run_id("test", tid)]
    assert v["parent_run_id"] == "profiles:coder"
    assert v["agent_id"] == "coder"


def test_sync_board_projects_cards_under_profiles_with_mapped_state(env):
    conn = env
    running = kanban_bridge.assign_card(conn, title="Build feature", profile="coder", board="test")
    done = kanban_db.create_task(conn, title="Research done", assignee="atlas")
    kanban_db.complete_task(conn, done, result="ok")
    blocked = kanban_db.create_task(conn, title="Strategy stuck", assignee="sage")
    kanban_db.block_task(conn, blocked, reason="stuck")

    n = kanban_bridge.sync_board_to_spine(conn, "test")
    assert n == 3

    views = _views()
    assert views[kanban_bridge.card_run_id("test", running)]["state"] in ("running", "pending")
    done_v = views[kanban_bridge.card_run_id("test", done)]
    assert done_v["state"] == "completed"
    assert done_v["parent_run_id"] == "profiles:atlas"
    blocked_v = views[kanban_bridge.card_run_id("test", blocked)]
    assert blocked_v["state"] == "stalled"
    assert blocked_v["parent_run_id"] == "profiles:sage"


def test_unknown_kanban_status_maps_to_unknown_not_running(env):
    conn = env
    tid = kanban_db.create_task(conn, title="Triaged", assignee="coder", triage=True)
    kanban_bridge.sync_board_to_spine(conn, "test")
    # triage maps to pending (a real backlog state), never silently to running.
    assert _views()[kanban_bridge.card_run_id("test", tid)]["state"] == "pending"
