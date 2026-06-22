"""Tests for the oc_agents background-session plugin (Agent View).

Stdlib + pytest only, no network, no LLM. The DB is isolated to a tmp file and
the agent build is replaced with a fake, so the supervisor/worker/CLI state
machine is exercised deterministically without spawning real model runs.
"""

from __future__ import annotations

import os

import pytest

from plugins.oc_agents import cli, db, supervisor, worker


@pytest.fixture()
def agents_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_AGENTS_DB", str(tmp_path / "oc_agents.db"))
    for attr in ("conn", "path"):
        if hasattr(db._local, attr):
            try:
                if attr == "conn" and db._local.conn is not None:
                    db._local.conn.close()
            except Exception:
                pass
            delattr(db._local, attr)
    yield
    for attr in ("conn", "path"):
        if hasattr(db._local, attr):
            try:
                if attr == "conn" and db._local.conn is not None:
                    db._local.conn.close()
            except Exception:
                pass
            delattr(db._local, attr)


# --------------------------------------------------------------------------- #
# DB layer
# --------------------------------------------------------------------------- #

def test_session_lifecycle(agents_db):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="do a thing", name="")
    s = db.get_session(sid)
    assert s is not None
    assert s["status"] == db.STATE_PENDING
    assert s["name"] == "do-a-thing"  # derived slug

    db.set_pid(sid, 4242)
    db.mark_working(sid, agent_session_id="sess-xyz")
    s = db.get_session(sid)
    assert s["status"] == db.STATE_WORKING
    assert s["agent_session_id"] == "sess-xyz"
    assert s["started_at"] is not None

    db.update_summary(sid, "running tool: read_file", api_calls=3)
    assert db.get_session(sid)["last_summary"] == "running tool: read_file"
    assert db.get_session(sid)["api_calls"] == 3

    db.finish_session(sid, db.STATE_COMPLETED, result="all done", api_calls=5)
    s = db.get_session(sid)
    assert s["status"] == db.STATE_COMPLETED
    assert s["result"] == "all done"
    assert s["ended_at"] is not None


def test_list_and_counts_and_pin(agents_db):
    a = db.new_session_id(); db.create_session(session_id=a, prompt="a")
    b = db.new_session_id(); db.create_session(session_id=b, prompt="b")
    db.finish_session(b, db.STATE_COMPLETED, result="x")

    live_only = db.list_sessions(include_done=False)
    assert {s["id"] for s in live_only} == {a}
    all_sessions = db.list_sessions(include_done=True)
    assert {s["id"] for s in all_sessions} == {a, b}

    db.set_pinned(b, True)
    ordered = db.list_sessions(include_done=True)
    assert ordered[0]["id"] == b  # pinned floats to top

    c = db.counts()
    assert c.get(db.STATE_PENDING) == 1
    assert c.get(db.STATE_COMPLETED) == 1


def test_delete_session(agents_db):
    sid = db.new_session_id(); db.create_session(session_id=sid, prompt="a")
    assert db.delete_session(sid) is True
    assert db.get_session(sid) is None
    assert db.delete_session("nope") is False


# --------------------------------------------------------------------------- #
# Liveness reconciliation — the "supervisor without a daemon"
# --------------------------------------------------------------------------- #

def test_reconcile_demotes_dead_pid(agents_db):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="a")
    db.set_pid(sid, 999999)  # almost certainly not a live pid
    db.mark_working(sid)
    assert db.get_session(sid)["status"] == db.STATE_WORKING

    demoted = db.reconcile_liveness()
    assert demoted == 1
    assert db.get_session(sid)["status"] == db.STATE_FAILED


def test_reconcile_keeps_live_pid(agents_db):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="a")
    db.set_pid(sid, os.getpid())  # this test process is alive
    db.mark_working(sid)
    assert db.reconcile_liveness() == 0
    assert db.get_session(sid)["status"] == db.STATE_WORKING


# --------------------------------------------------------------------------- #
# Supervisor dispatch / stop (mock the detached subprocess)
# --------------------------------------------------------------------------- #

class _FakeProc:
    def __init__(self, pid=12345):
        self.pid = pid


def test_dispatch_creates_row_and_spawns(agents_db, monkeypatch):
    spawned = {}

    def fake_popen(cmd, **kwargs):
        spawned["cmd"] = cmd
        spawned["env_has_db"] = "HERMES_OC_AGENTS_DB" in kwargs.get("env", {})
        return _FakeProc(pid=4321)

    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    sid = supervisor.dispatch("investigate the bug", name="bughunt")

    s = db.get_session(sid)
    assert s is not None and s["name"] == "bughunt"
    assert s["pid"] == 4321
    # The worker command targets this session id and carries the DB pin.
    assert "_worker" in spawned["cmd"] and sid in spawned["cmd"]
    assert spawned["env_has_db"] is True


def test_dispatch_spawn_failure_marks_failed(agents_db, monkeypatch):
    def boom(cmd, **kwargs):
        raise OSError("no fork for you")

    monkeypatch.setattr(supervisor.subprocess, "Popen", boom)
    with pytest.raises(OSError):
        supervisor.dispatch("x")
    # The row was created then marked failed.
    sessions = db.list_sessions(include_done=True)
    assert sessions and sessions[0]["status"] == db.STATE_FAILED


def test_stop_signals_and_marks(agents_db, monkeypatch):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="a")
    db.set_pid(sid, os.getpid())
    db.mark_working(sid)

    killed = {}
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: killed.setdefault("pid", pid))
    assert supervisor.stop(sid) is True
    assert killed["pid"] == os.getpid()
    assert db.get_session(sid)["status"] == db.STATE_STOPPED
    # Stopping an already-finished session is a no-op.
    assert supervisor.stop(sid) is False


# --------------------------------------------------------------------------- #
# Worker state machine (fake agent — no LLM)
# --------------------------------------------------------------------------- #

def test_worker_runs_to_completion_with_fake_agent(agents_db, monkeypatch, tmp_path):
    monkeypatch.setenv("OC_AGENTS_FAKE_AGENT", "1")
    sid = db.new_session_id()
    log_path = str(tmp_path / f"{sid}.log")
    db.create_session(session_id=sid, prompt="summarize the repo", log_path=log_path)

    rc = worker.run_worker(sid)
    assert rc == 0
    s = db.get_session(sid)
    assert s["status"] == db.STATE_COMPLETED
    assert "[fake] handled" in (s["result"] or "")
    assert s["pid"] == os.getpid()


def test_worker_unknown_session(agents_db):
    assert worker.run_worker("does-not-exist") == 2


def test_worker_handles_build_failure(agents_db, monkeypatch, tmp_path):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="x", log_path=str(tmp_path / "l.log"))

    def boom(row):
        raise RuntimeError("provider down")

    monkeypatch.setattr(worker, "_build_headless_agent", boom)
    rc = worker.run_worker(sid)
    assert rc == 1
    s = db.get_session(sid)
    assert s["status"] == db.STATE_FAILED
    assert "provider down" in (s["error"] or "")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _make_parser():
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    node = subparsers.add_parser("agents")
    cli.setup(node)
    return parser


def test_cli_dispatch_list_show_stop(agents_db, monkeypatch, capsys):
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda cmd, **kw: _FakeProc(os.getpid()))
    parser = _make_parser()

    rc = cli.handle(parser.parse_args(["agents", "dispatch", "do work", "--name", "w1"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "dispatched" in out

    rc = cli.handle(parser.parse_args(["agents", "list", "--all"]))
    assert rc == 0
    listing = capsys.readouterr().out
    assert "w1" in listing

    # Find the session id and show it.
    sid = db.list_sessions(include_done=True)[0]["id"]
    rc = cli.handle(parser.parse_args(["agents", "show", sid]))
    assert rc == 0
    assert sid in capsys.readouterr().out

    # Stop it (pid is this process; kill is mocked).
    monkeypatch.setattr(supervisor.os, "kill", lambda pid, sig: None)
    rc = cli.handle(parser.parse_args(["agents", "stop", sid]))
    assert rc == 0
    assert db.get_session(sid)["status"] == db.STATE_STOPPED


def test_cli_show_unknown(agents_db, capsys):
    parser = _make_parser()
    rc = cli.handle(parser.parse_args(["agents", "show", "nope"]))
    assert rc == 2


def test_cli_rm_guards_live_session(agents_db, capsys):
    sid = db.new_session_id()
    db.create_session(session_id=sid, prompt="a")
    db.mark_working(sid)
    parser = _make_parser()
    rc = cli.handle(parser.parse_args(["agents", "rm", sid]))
    assert rc == 1  # can't remove a live session
    db.finish_session(sid, db.STATE_COMPLETED)
    rc = cli.handle(parser.parse_args(["agents", "rm", sid]))
    assert rc == 0
    assert db.get_session(sid) is None


def test_cli_dispatch_with_agent_type(agents_db, tmp_path, monkeypatch):
    """`oc agents dispatch --agent <type>` seeds the session from a definition."""
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    (tmp_path / "rev.md").write_text(
        "---\nname: reviewer\ntoolsets: [read_file]\nmodel: claude-haiku-4-5\n---\nYou are a reviewer."
    )
    captured = {}

    def fake_dispatch(prompt, **kw):
        captured["prompt"] = prompt
        captured.update(kw)
        return "bg1"

    monkeypatch.setattr(supervisor, "dispatch", fake_dispatch)
    parser = _make_parser()
    rc = cli.handle(parser.parse_args(["agents", "dispatch", "review the diff", "--agent", "reviewer"]))
    assert rc == 0
    assert captured["model"] == "claude-haiku-4-5"
    assert captured["toolsets"] == ["read_file"]
    assert "You are a reviewer." in captured["prompt"]
    assert "review the diff" in captured["prompt"]


def test_worker_applies_startup_permission_mode(agents_db, monkeypatch):
    """A teammate/bg session spawned with a permission mode honors it process-wide."""
    from tools import permission_rules

    monkeypatch.setenv("HERMES_PERMISSION_MODE", "plan")
    try:
        applied = worker._apply_startup_permission_mode()
        assert applied == "plan"
        assert permission_rules.get_effective_mode() == "plan"
    finally:
        permission_rules.set_global_mode(None)


def test_worker_no_permission_mode_is_noop(agents_db, monkeypatch):
    monkeypatch.delenv("HERMES_PERMISSION_MODE", raising=False)
    assert worker._apply_startup_permission_mode() is None


def test_cli_dispatch_agent_forwards_memory_and_permission(agents_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    (tmp_path / "rev.md").write_text(
        "---\nname: reviewer\nmemory: project\npermissionMode: plan\n---\nReview."
    )
    captured = {}
    monkeypatch.setattr(supervisor, "dispatch", lambda prompt, **kw: captured.update(kw) or "bg2")
    parser = _make_parser()
    rc = cli.handle(parser.parse_args(["agents", "dispatch", "do it", "--agent", "reviewer", "--cwd", str(tmp_path)]))
    assert rc == 0
    env = captured["extra_env"]
    assert env["HERMES_PERMISSION_MODE"] == "plan"
    assert env["HERMES_MEMORY_DIR"].endswith("/.hermes/agent-memory/reviewer")


def test_plan_mode_actually_blocks_mutating_tool(agents_db, monkeypatch):
    """Lock the invariant: a worker started in plan mode genuinely blocks a
    mutating tool (and allows a read-only one) — YOLO must not bypass it."""
    from tools import permission_rules

    monkeypatch.setenv("HERMES_PERMISSION_MODE", "plan")
    os.environ.setdefault("HERMES_YOLO_MODE", "1")
    try:
        worker._apply_startup_permission_mode()
        assert permission_rules.pre_tool_block_message("write_file", {}, "") is not None
        assert permission_rules.pre_tool_block_message("read_file", {}, "") is None
    finally:
        permission_rules.set_global_mode(None)
