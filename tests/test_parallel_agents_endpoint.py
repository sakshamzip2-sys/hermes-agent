"""Tests for the GET /api/parallel-agents snapshot endpoint.

Exercises ``APIServerAdapter.build_parallel_agents_snapshot()`` (the testable
helper the aiohttp handler wraps): the happy path with seeded plugin DBs, and
the graceful-degradation path when a plugin's DB layer raises.
"""

from __future__ import annotations

import pytest

from gateway.platforms.api_server import APIServerAdapter


@pytest.fixture()
def isolated_plugin_dbs(tmp_path, monkeypatch):
    """Point all three plugin DBs at temp files and reset their connections."""
    monkeypatch.setenv("HERMES_OC_FLOW_DB", str(tmp_path / "oc_flow.db"))
    monkeypatch.setenv("HERMES_OC_AGENTS_DB", str(tmp_path / "oc_agents.db"))
    monkeypatch.setenv("HERMES_OC_TEAMS_DB", str(tmp_path / "oc_teams.db"))

    from plugins.oc_agents import db as agents_db
    from plugins.oc_flow import db as flow_db
    from plugins.oc_teams import db as teams_db

    for mod in (flow_db, agents_db, teams_db):
        for attr in ("conn", "path"):
            if hasattr(mod._local, attr):
                try:
                    if attr == "conn" and mod._local.conn is not None:
                        mod._local.conn.close()
                except Exception:
                    pass
                delattr(mod._local, attr)
    yield {"flow": flow_db, "agents": agents_db, "teams": teams_db}


def test_snapshot_shape_when_empty(isolated_plugin_dbs):
    snap = APIServerAdapter.build_parallel_agents_snapshot()
    assert snap["object"] == "hermes.parallel_agents"
    assert set(snap.keys()) == {"object", "flows", "agents", "teams", "errors", "timestamp"}
    assert snap["flows"] == [] and snap["agents"] == [] and snap["teams"] == []
    assert snap["errors"] == {"flows": None, "agents": None, "teams": None}
    assert isinstance(snap["timestamp"], int)


def test_snapshot_reflects_seeded_data(isolated_plugin_dbs):
    flow_db = isolated_plugin_dbs["flow"]
    agents_db = isolated_plugin_dbs["agents"]
    teams_db = isolated_plugin_dbs["teams"]

    flow_db.create_run(run_id=flow_db.new_run_id(), name="my-flow")
    agents_db.create_session(session_id=agents_db.new_session_id(), prompt="bg task")
    tid = teams_db.new_team_id()
    teams_db.create_team(tid, "my-team", goal="ship")
    teams_db.create_task(tid, "task one")

    snap = APIServerAdapter.build_parallel_agents_snapshot()
    assert len(snap["flows"]) == 1 and snap["flows"][0]["name"] == "my-flow"
    assert len(snap["agents"]) == 1 and snap["agents"][0]["prompt"] == "bg task"
    assert len(snap["teams"]) == 1
    team = snap["teams"][0]
    assert team["name"] == "my-team"
    assert team["goal"] == "ship"
    assert team["member_count"] == 1  # the lead
    assert team["tasks_total"] == 1
    assert team["task_counts"] == {"pending": 1}
    assert snap["errors"] == {"flows": None, "agents": None, "teams": None}


def test_snapshot_degrades_gracefully_on_plugin_error(isolated_plugin_dbs, monkeypatch):
    # Simulate the flows section failing (e.g. a plugin DB error) — the endpoint
    # must still return the other sections and record the error, never raise.
    from plugins.oc_flow import db as flow_db

    def boom(*a, **k):
        raise RuntimeError("flow db exploded")

    monkeypatch.setattr(flow_db, "list_runs", boom)

    snap = APIServerAdapter.build_parallel_agents_snapshot()
    assert snap["flows"] == []
    assert snap["errors"]["flows"] is not None and "exploded" in snap["errors"]["flows"]
    # The other sections are unaffected.
    assert snap["errors"]["agents"] is None
    assert snap["errors"]["teams"] is None
