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


# ---------------------------------------------------------------------------
# Drill-down detail builders
# ---------------------------------------------------------------------------


def test_flow_detail_assembles_run_phases_agents_logs(isolated_plugin_dbs):
    flow_db = isolated_plugin_dbs["flow"]
    rid = flow_db.new_run_id()
    flow_db.create_run(run_id=rid, name="review", meta={"phases": ["Map", "Verify"]})
    flow_db.add_phase(rid, 0, "Map")
    flow_db.add_phase(rid, 1, "Verify")
    flow_db.start_agent(rid, 0, label="scanner", phase="Map", model="x")
    flow_db.finish_agent(rid, 0, status="completed", result={"ok": True}, api_calls=3)
    flow_db.add_log(rid, "phase Map started")
    flow_db.finish_run(rid, "completed", result={"confirmed": []})

    detail = APIServerAdapter.build_flow_detail(rid)
    assert detail is not None
    assert detail["object"] == "hermes.flow_detail"
    assert detail["run"]["id"] == rid
    assert [p["title"] for p in detail["phases"]] == ["Map", "Verify"]
    assert len(detail["agents"]) == 1 and detail["agents"][0]["label"] == "scanner"
    assert detail["agents"][0]["api_calls"] == 3
    assert any("Map started" in lg["message"] for lg in detail["logs"])
    assert detail["result"] == {"confirmed": []}
    assert detail["errors"] == {}


def test_flow_detail_missing_returns_none(isolated_plugin_dbs):
    assert APIServerAdapter.build_flow_detail("flow_does_not_exist") is None


def test_agent_detail_includes_log_tail_and_chat_session(isolated_plugin_dbs):
    agents_db = isolated_plugin_dbs["agents"]
    # Real agent logs live under logs_dir(); the detail builder refuses to read
    # log paths outside it, so the test log must live there too.
    logf = agents_db.logs_dir() / "agent.log"
    logf.write_text("line one\nline two\n", encoding="utf-8")
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="bg task", log_path=str(logf))
    agents_db.mark_working(sid, "hermes_sess_abc")

    detail = APIServerAdapter.build_agent_detail(sid)
    assert detail is not None
    assert detail["object"] == "hermes.agent_detail"
    assert detail["session"]["id"] == sid
    assert "line two" in detail["log_tail"]
    assert detail["chat_session_id"] == "hermes_sess_abc"
    assert detail["errors"]["log"] is None


def test_agent_detail_missing_returns_none(isolated_plugin_dbs):
    assert APIServerAdapter.build_agent_detail("nope") is None


def test_agent_events_add_list_and_filter(isolated_plugin_dbs):
    agents_db = isolated_plugin_dbs["agents"]
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="work")
    agents_db.add_event(sid, "started tool: search")
    agents_db.add_event(sid, "tool done: search")
    agents_db.add_event(sid, "thinking…", kind="thinking")
    # Empty events are ignored.
    agents_db.add_event(sid, "")

    events = agents_db.list_events(sid)
    assert [e["text"] for e in events] == [
        "started tool: search",
        "tool done: search",
        "thinking…",
    ]
    assert events[2]["kind"] == "thinking"
    # Incremental polling: only events after a cursor id.
    after = agents_db.list_events(sid, after_id=events[0]["id"])
    assert [e["text"] for e in after] == ["tool done: search", "thinking…"]
    # Limit caps the result.
    assert len(agents_db.list_events(sid, limit=1)) == 1


def test_agent_detail_includes_events(isolated_plugin_dbs):
    agents_db = isolated_plugin_dbs["agents"]
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="work")
    agents_db.add_event(sid, "calling terminal")
    agents_db.add_event(sid, "wrote file")

    detail = APIServerAdapter.build_agent_detail(sid)
    assert detail is not None
    assert [e["text"] for e in detail["events"]] == ["calling terminal", "wrote file"]
    assert detail["errors"]["events"] is None


def test_delete_session_clears_events(isolated_plugin_dbs):
    agents_db = isolated_plugin_dbs["agents"]
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="work")
    agents_db.add_event(sid, "event one")
    assert len(agents_db.list_events(sid)) == 1
    assert agents_db.delete_session(sid) is True
    assert agents_db.list_events(sid) == []


def test_add_event_fifo_cap_bounds_growth(isolated_plugin_dbs, monkeypatch):
    agents_db = isolated_plugin_dbs["agents"]
    monkeypatch.setattr(agents_db, "EVENTS_PER_SESSION_CAP", 5)
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="work")
    # Fresh isolated DB → ids 1..100 are all this session's; the prune fires at
    # the 100th insert and keeps only the most recent CAP events.
    for i in range(100):
        agents_db.add_event(sid, f"event {i}")
    kept = agents_db.list_events(sid)
    assert len(kept) <= 5
    assert kept[-1]["text"] == "event 99"  # newest retained


def test_agent_detail_handles_missing_log_path(isolated_plugin_dbs):
    agents_db = isolated_plugin_dbs["agents"]
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="no log")
    detail = APIServerAdapter.build_agent_detail(sid)
    assert detail is not None
    assert detail["log_tail"] == ""
    assert detail["chat_session_id"] == ""


def test_team_detail_enriches_members_with_chat_session(isolated_plugin_dbs):
    teams_db = isolated_plugin_dbs["teams"]
    agents_db = isolated_plugin_dbs["agents"]
    # A teammate is backed by a background session that carries a hermes
    # chat session id — the team detail must surface it for click-to-chat.
    agents_db.create_session(session_id="bg1", prompt="teammate work")
    agents_db.mark_working("bg1", "hermes_sess_team1")

    tid = teams_db.new_team_id()
    teams_db.create_team(tid, "shippers", goal="ship it")
    teams_db.add_member(tid, "alice", kind="teammate", bg_session_id="bg1")
    teams_db.create_task(tid, "build feature")
    teams_db.send_message(tid, "user", "*", "go team")

    detail = APIServerAdapter.build_team_detail(tid)
    assert detail is not None
    assert detail["object"] == "hermes.team_detail"
    assert detail["team"]["name"] == "shippers"
    alice = next(m for m in detail["members"] if m["name"] == "alice")
    assert alice["chat_session_id"] == "hermes_sess_team1"
    lead = next(m for m in detail["members"] if m["kind"] == "lead")
    assert lead["chat_session_id"] == ""
    assert len(detail["tasks"]) == 1
    assert any(m["body"] == "go team" for m in detail["messages"])
    assert detail["errors"] == {}


def test_team_detail_missing_returns_none(isolated_plugin_dbs):
    assert APIServerAdapter.build_team_detail("team_nope") is None


# ---------------------------------------------------------------------------
# Control
# ---------------------------------------------------------------------------


def test_stop_flow_is_idempotent(isolated_plugin_dbs):
    flow_db = isolated_plugin_dbs["flow"]
    rid = flow_db.new_run_id()
    flow_db.create_run(run_id=rid, name="long-flow")  # status 'pending' => stoppable

    first = APIServerAdapter.stop_flow(rid)
    assert first == {"found": True, "stopped": True, "status": "stopped"}
    # Already terminal — second stop is a no-op, not an error.
    second = APIServerAdapter.stop_flow(rid)
    assert second["found"] is True and second["stopped"] is False
    assert second["status"] == "stopped"


def test_stop_flow_unknown_run(isolated_plugin_dbs):
    assert APIServerAdapter.stop_flow("flow_ghost") == {"found": False, "stopped": False}


# ---------------------------------------------------------------------------
# _tail_file helper
# ---------------------------------------------------------------------------


def test_tail_file_small_returns_whole(tmp_path):
    f = tmp_path / "s.log"
    f.write_text("hello\nworld\n", encoding="utf-8")
    assert APIServerAdapter._tail_file(str(f), 1024) == "hello\nworld\n"


def test_tail_file_large_drops_partial_first_line(tmp_path):
    f = tmp_path / "big.log"
    lines = [f"line-{i:04d}" for i in range(1000)]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tail = APIServerAdapter._tail_file(str(f), 200)
    assert len(tail) <= 200
    # The first retained line must be whole (no leading partial fragment).
    assert tail.startswith("line-")
    assert tail.rstrip().endswith("line-0999")


def test_tail_file_absent_returns_empty(tmp_path):
    assert APIServerAdapter._tail_file(str(tmp_path / "missing.log")) == ""


def test_tail_file_allowed_dir_blocks_traversal(tmp_path):
    allowed = tmp_path / "logs"
    allowed.mkdir()
    inside = allowed / "ok.log"
    inside.write_text("inside\n", encoding="utf-8")
    outside = tmp_path / "secret.log"
    outside.write_text("TOP SECRET\n", encoding="utf-8")

    # A path within the allowed dir reads normally.
    assert "inside" in APIServerAdapter._tail_file(str(inside), allowed_dir=str(allowed))
    # A path outside the allowed dir is refused (empty), even via traversal.
    assert APIServerAdapter._tail_file(str(outside), allowed_dir=str(allowed)) == ""
    traversal = allowed / ".." / "secret.log"
    assert APIServerAdapter._tail_file(str(traversal), allowed_dir=str(allowed)) == ""


def test_agent_detail_rejects_log_path_outside_logs_dir(isolated_plugin_dbs, tmp_path):
    agents_db = isolated_plugin_dbs["agents"]
    # A tampered log_path pointing outside the agent logs dir must not leak.
    secret = tmp_path / "passwd"
    secret.write_text("root:x:0:0", encoding="utf-8")
    sid = agents_db.new_session_id()
    agents_db.create_session(session_id=sid, prompt="x", log_path=str(secret))
    detail = APIServerAdapter.build_agent_detail(sid)
    assert detail is not None
    assert detail["log_tail"] == ""


def test_list_accessors_honor_limit(isolated_plugin_dbs):
    flow_db = isolated_plugin_dbs["flow"]
    teams_db = isolated_plugin_dbs["teams"]
    rid = flow_db.new_run_id()
    flow_db.create_run(run_id=rid, name="capped")
    for i in range(3):
        flow_db.add_phase(rid, i, f"p{i}")
        flow_db.start_agent(rid, i, label=f"a{i}")
    assert len(flow_db.list_phases(rid, limit=2)) == 2
    assert len(flow_db.list_agents(rid, limit=1)) == 1
    # Default (no limit) still returns everything — backward compatible.
    assert len(flow_db.list_phases(rid)) == 3

    tid = teams_db.new_team_id()
    teams_db.create_team(tid, "t")
    teams_db.add_member(tid, "m1")
    teams_db.add_member(tid, "m2")
    teams_db.create_task(tid, "t1")
    teams_db.create_task(tid, "t2")
    assert len(teams_db.list_members(tid, limit=1)) == 1
    assert len(teams_db.list_tasks(tid, limit=1)) == 1


def test_team_detail_member_key_survives_enrichment_error(
    isolated_plugin_dbs, monkeypatch
):
    teams_db = isolated_plugin_dbs["teams"]
    agents_db = isolated_plugin_dbs["agents"]
    tid = teams_db.new_team_id()
    teams_db.create_team(tid, "resilient")
    teams_db.add_member(tid, "alice", kind="teammate", bg_session_id="bg-x")
    teams_db.add_member(tid, "bob", kind="teammate", bg_session_id="bg-y")

    def boom(*a, **k):
        raise RuntimeError("agents db exploded")

    monkeypatch.setattr(agents_db, "get_session", boom)

    detail = APIServerAdapter.build_team_detail(tid)
    assert detail is not None
    # Every member still carries the key (defaulted to ""), and the error is
    # recorded rather than dropping the field from later members.
    for m in detail["members"]:
        assert "chat_session_id" in m
        assert m["chat_session_id"] == ""
    assert "member_sessions" in detail["errors"]
