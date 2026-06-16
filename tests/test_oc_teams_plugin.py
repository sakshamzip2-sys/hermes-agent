"""Tests for the oc_teams agent-teams plugin.

Stdlib + pytest only, no network, no LLM. Isolated DB per test. Covers the
shared task list (atomic claim under real thread contention, dependency
gating), the mailbox, the coordinator (with injected spawn/stop), the
service-gated tools, and the CLI.
"""

from __future__ import annotations

import threading

import pytest

from plugins.oc_teams import cli, coordinator, db, tools


@pytest.fixture()
def teams_db(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_TEAMS_DB", str(tmp_path / "oc_teams.db"))
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
# Teams + members
# --------------------------------------------------------------------------- #

def test_create_team_and_members(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "research", goal="figure it out")
    t = db.get_team(tid)
    assert t["name"] == "research" and t["status"] == "active"
    members = db.list_members(tid)
    assert len(members) == 1 and members[0]["kind"] == db.MEMBER_LEAD

    assert db.add_member(tid, "alice", role="ux", kind=db.MEMBER_TEAMMATE) is True
    assert db.add_member(tid, "alice") is False  # duplicate name rejected
    assert len(db.active_teammates(tid)) == 1


# --------------------------------------------------------------------------- #
# Shared task list — claim CAS + dependencies
# --------------------------------------------------------------------------- #

def test_only_one_member_wins_a_task(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    task = db.create_task(tid, "build the thing")
    # First claim wins; subsequent claims fail.
    assert db.claim_task(task, "alice") is True
    assert db.claim_task(task, "bob") is False
    got = db.get_task(task)
    assert got["owner"] == "alice" and got["status"] == db.TASK_IN_PROGRESS


def test_concurrent_claim_exactly_one_winner(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    task = db.create_task(tid, "contended")

    winners = []
    lock = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(name):
        barrier.wait()  # maximize contention
        if db.claim_task(task, name):
            with lock:
                winners.append(name)

    threads = [threading.Thread(target=worker, args=(f"m{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1, f"expected exactly one winner, got {winners}"


def test_dependency_gating(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    a = db.create_task(tid, "first")
    b = db.create_task(tid, "second", depends_on=[a])

    # b is not claimable until a completes.
    claimable_ids = {t["id"] for t in db.claimable_tasks(tid)}
    assert a in claimable_ids and b not in claimable_ids
    assert db.claim_task(b, "x") is False  # blocked by dep

    db.claim_task(a, "x")
    db.complete_task(a)
    claimable_ids = {t["id"] for t in db.claimable_tasks(tid)}
    assert b in claimable_ids
    assert db.claim_task(b, "x") is True


def test_complete_task_idempotent(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    task = db.create_task(tid, "x")
    assert db.complete_task(task) is True
    assert db.complete_task(task) is False  # already completed


def test_complete_task_ownership_guard(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    task = db.create_task(tid, "owned")
    db.claim_task(task, "alice")

    # bob cannot complete alice's claimed task; alice can.
    assert db.complete_task(task, member="bob") is False
    assert db.get_task(task)["status"] == db.TASK_IN_PROGRESS
    assert db.complete_task(task, member="alice") is True

    # A member may complete an unclaimed task (did it inline);
    # the lead/CLI override (empty member) can complete anything not done.
    t2 = db.create_task(tid, "unclaimed")
    assert db.complete_task(t2, member="carol") is True
    t3 = db.create_task(tid, "lead-forced")
    db.claim_task(t3, "alice")
    assert db.complete_task(t3) is True  # member="" → override


# --------------------------------------------------------------------------- #
# Mailbox
# --------------------------------------------------------------------------- #

def test_mailbox_direct_and_broadcast(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    db.add_member(tid, "alice")
    db.add_member(tid, "bob")

    db.send_message(tid, "lead", "alice", "do task 1")
    db.send_message(tid, "lead", "*", "standup in 5")

    alice = db.read_inbox(tid, "alice")
    bodies = {m["body"] for m in alice}
    assert bodies == {"do task 1", "standup in 5"}
    # Marked read — second read is empty.
    assert db.read_inbox(tid, "alice") == []

    # Bob sees only the broadcast (not alice's direct message).
    bob = db.read_inbox(tid, "bob")
    assert {m["body"] for m in bob} == {"standup in 5"}


def test_inbox_excludes_own_messages(teams_db):
    tid = db.new_team_id()
    db.create_team(tid, "t")
    db.add_member(tid, "alice")
    db.send_message(tid, "alice", "*", "hi all")  # alice's own broadcast
    assert db.read_inbox(tid, "alice") == []  # don't deliver to self


# --------------------------------------------------------------------------- #
# Coordinator (inject spawn/stop — no real processes)
# --------------------------------------------------------------------------- #

def test_spawn_teammate_registers_and_dispatches(teams_db):
    tid = coordinator.create_team("build", goal="ship it")
    calls = {}

    def fake_dispatch(prompt, **kwargs):
        calls["prompt"] = prompt
        calls["env"] = kwargs.get("extra_env")
        calls["kind"] = kwargs.get("kind")
        return "bg123"

    bg = coordinator.spawn_teammate(tid, "alice", "review the auth module", role="security", dispatch_fn=fake_dispatch)
    assert bg == "bg123"
    member = db.get_member(tid, "alice")
    assert member["bg_session_id"] == "bg123" and member["role"] == "security"
    # The teammate's env carries the team id + name, and the protocol is embedded.
    assert calls["env"]["HERMES_TEAM_ID"] == tid
    assert calls["env"]["HERMES_TEAM_MEMBER"] == "alice"
    assert calls["kind"] == "teammate"
    assert "team_claim_task" in calls["prompt"] and "review the auth module" in calls["prompt"]


def test_spawn_rejects_duplicate_member(teams_db):
    tid = coordinator.create_team("t")
    coordinator.spawn_teammate(tid, "alice", "x", dispatch_fn=lambda *a, **k: "bg1")
    with pytest.raises(ValueError):
        coordinator.spawn_teammate(tid, "alice", "y", dispatch_fn=lambda *a, **k: "bg2")


def test_cleanup_refuses_with_active_then_force(teams_db):
    tid = coordinator.create_team("t")
    coordinator.spawn_teammate(tid, "alice", "x", dispatch_fn=lambda *a, **k: "bg1")

    with pytest.raises(RuntimeError):
        coordinator.cleanup_team(tid)  # active teammate blocks cleanup

    stopped = []
    assert coordinator.cleanup_team(tid, force=True, stop_fn=lambda sid: stopped.append(sid)) is True
    assert stopped == ["bg1"]
    assert db.get_team(tid)["status"] == "cleaned"


def test_shutdown_teammate(teams_db):
    tid = coordinator.create_team("t")
    coordinator.spawn_teammate(tid, "alice", "x", dispatch_fn=lambda *a, **k: "bg1")
    stopped = []
    assert coordinator.shutdown_teammate(tid, "alice", stop_fn=lambda sid: stopped.append(sid)) is True
    assert stopped == ["bg1"]
    assert db.get_member(tid, "alice")["status"] == "shutdown"
    assert coordinator.shutdown_teammate(tid, "ghost") is False


# --------------------------------------------------------------------------- #
# Service-gated tools
# --------------------------------------------------------------------------- #

def test_team_tools_gated_on_env(monkeypatch):
    monkeypatch.delenv("HERMES_TEAM_ID", raising=False)
    assert tools._team_mode_active() is False
    monkeypatch.setenv("HERMES_TEAM_ID", "team_x")
    assert tools._team_mode_active() is True


def test_team_tools_end_to_end(teams_db, monkeypatch):
    import json

    tid = coordinator.create_team("t", goal="g")
    db.add_member(tid, "alice")
    # Simulate alice's session env.
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")

    created = json.loads(tools._handle_create_task({"subject": "do X"}))
    task_id = created["task_id"]

    listed = json.loads(tools._handle_list_tasks({"status": "claimable"}))
    assert any(t["id"] == task_id for t in listed["tasks"])

    claimed = json.loads(tools._handle_claim_task({"task_id": task_id}))
    assert claimed["claimed"] is True and claimed["owner"] == "alice"

    done = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "did it"}))
    assert done["completed"] is True

    # The completion result was mailed to the lead.
    lead_inbox = json.loads(tools._handle_read_inbox({}))  # alice reads her own (empty)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "lead")
    lead_msgs = json.loads(tools._handle_read_inbox({}))
    assert any("did it" in m["body"] for m in lead_msgs["messages"])


def test_team_tools_require_team_context(monkeypatch):
    import json

    monkeypatch.delenv("HERMES_TEAM_ID", raising=False)
    out = json.loads(tools._handle_status({}))
    assert "error" in out  # tool_error when not in a team


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _make_parser():
    import argparse

    parser = argparse.ArgumentParser()
    node = parser.add_subparsers(dest="command").add_parser("team")
    cli.setup(node)
    return parser


def test_cli_full_flow(teams_db, capsys, monkeypatch):
    parser = _make_parser()

    # create
    assert cli.handle(parser.parse_args(["team", "create", "alpha", "--goal", "win"])) == 0
    out = capsys.readouterr().out
    tid = out.split()[1].rstrip(":")  # "team <id>: created"

    # task-add, tasks, task-claim, task-done
    assert cli.handle(parser.parse_args(["team", "task-add", tid, "do the thing"])) == 0
    capsys.readouterr()
    assert cli.handle(parser.parse_args(["team", "tasks", tid])) == 0
    listing = capsys.readouterr().out
    task_id = listing.split()[0]

    assert cli.handle(parser.parse_args(["team", "task-claim", tid, task_id, "alice"])) == 0
    assert cli.handle(parser.parse_args(["team", "task-done", tid, task_id])) == 0

    # send + inbox
    assert cli.handle(parser.parse_args(["team", "send", tid, "lead", "alice", "great work"])) == 0
    capsys.readouterr()
    assert cli.handle(parser.parse_args(["team", "inbox", tid, "alice"])) == 0
    assert "great work" in capsys.readouterr().out

    # show + list
    assert cli.handle(parser.parse_args(["team", "show", tid])) == 0
    assert tid in capsys.readouterr().out


def test_cli_unknown_team(teams_db, capsys):
    parser = _make_parser()
    assert cli.handle(parser.parse_args(["team", "show", "team_nope"])) == 2


# --------------------------------------------------------------------------- #
# Team lifecycle hooks (quality gates) — Feature B
#
# Mirror Claude-Code's TaskCreated / TaskCompleted / TeammateIdle: a plugin (or
# shell-script) callback may veto a task creation/completion, or nudge an idle
# teammate to keep working. Veto uses the canonical v2 block contract
# ({"action":"block","message":...}) or the Claude-Code shape
# ({"decision":"block","reason":...}).
# --------------------------------------------------------------------------- #

@pytest.fixture()
def team_hook():
    """Register team lifecycle hook callbacks on the live plugin manager.

    Yields a register(name, callback) function; all registrations are removed
    on teardown so hooks never leak between tests.
    """
    from hermes_cli import plugins as _plugins

    pm = _plugins.get_plugin_manager()
    registered = []

    def _register(name, cb):
        pm._hooks.setdefault(name, []).append(cb)
        registered.append((name, cb))

    yield _register

    for name, cb in registered:
        try:
            pm._hooks.get(name, []).remove(cb)
        except ValueError:
            pass


def test_team_lifecycle_hooks_in_valid_hooks():
    from hermes_cli.plugins import VALID_HOOKS

    assert {"team_task_created", "team_task_completed", "team_teammate_idle"} <= VALID_HOOKS


def test_complete_task_hook_can_block(teams_db, team_hook, monkeypatch):
    import json

    tid = coordinator.create_team("t", goal="g")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")

    task_id = json.loads(tools._handle_create_task({"subject": "fix the bug"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    seen = {}

    def gate(**kw):
        seen.update(kw)
        return {"action": "block", "message": "attach a failing test as evidence"}

    team_hook("team_task_completed", gate)

    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "done"}))
    assert out["completed"] is False
    assert out["blocked"] is True
    assert out["reason"] == "attach a failing test as evidence"
    # The gate saw enough context to judge the work.
    assert seen.get("task_id") == task_id and seen.get("subject") == "fix the bug"
    assert seen.get("member") == "alice"
    # The task was NOT completed.
    assert db.get_task(task_id)["status"] == db.TASK_IN_PROGRESS


def test_complete_task_proceeds_when_hook_observes(teams_db, team_hook, monkeypatch):
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")

    task_id = json.loads(tools._handle_create_task({"subject": "x"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    # Observer returns None -> never blocks.
    team_hook("team_task_completed", lambda **kw: None)

    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "did it"}))
    assert out["completed"] is True
    assert db.get_task(task_id)["status"] == db.TASK_COMPLETED


def test_create_task_hook_can_block_cc_shape(teams_db, team_hook, monkeypatch):
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")

    # Claude-Code-style block shape must also be honoured.
    team_hook("team_task_created", lambda **kw: {"decision": "block", "reason": "no vague tasks"})

    out = json.loads(tools._handle_create_task({"subject": "do stuff"}))
    assert "error" in out
    assert "no vague tasks" in out["error"]
    # Nothing was created.
    assert db.list_tasks(tid) == []


def test_teammate_idle_hook_nudges_when_no_claimable(teams_db, team_hook, monkeypatch):
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")

    task_id = json.loads(tools._handle_create_task({"subject": "only task"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    team_hook("team_teammate_idle", lambda **kw: {"action": "block", "message": "also audit the logout path"})

    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "done"}))
    assert out["completed"] is True
    # No claimable tasks remain -> the idle hook fired and its nudge surfaced.
    assert out.get("idle_nudge") == "also audit the logout path"


def test_completion_gate_fails_open_when_hook_raises(teams_db, team_hook, monkeypatch):
    """A quality-gate callback that raises must NOT block completion (fail-open)."""
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")
    task_id = json.loads(tools._handle_create_task({"subject": "x"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    def boom(**kw):
        raise RuntimeError("gate plugin crashed")

    team_hook("team_task_completed", boom)
    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "done"}))
    assert out["completed"] is True  # crash swallowed -> proceeds


def test_completion_gate_first_veto_wins(teams_db, team_hook, monkeypatch):
    """An earlier observer (None) does not mask a later blocker."""
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")
    task_id = json.loads(tools._handle_create_task({"subject": "x"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    team_hook("team_task_completed", lambda **kw: None)
    team_hook("team_task_completed", lambda **kw: {"action": "block", "message": "second says no"})
    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "done"}))
    assert out["completed"] is False and out["reason"] == "second says no"


def test_completion_gate_malformed_block_message(teams_db, team_hook, monkeypatch):
    """A block with a non-string message still vetoes, with a safe default reason."""
    import json

    tid = coordinator.create_team("t")
    db.add_member(tid, "alice")
    monkeypatch.setenv("HERMES_TEAM_ID", tid)
    monkeypatch.setenv("HERMES_TEAM_MEMBER", "alice")
    task_id = json.loads(tools._handle_create_task({"subject": "x"}))["task_id"]
    tools._handle_claim_task({"task_id": task_id})

    team_hook("team_task_completed", lambda **kw: {"action": "block", "message": 123})
    out = json.loads(tools._handle_complete_task({"task_id": task_id, "result": "done"}))
    assert out["completed"] is False and out["reason"] == "blocked"


# --------------------------------------------------------------------------- #
# Reusable agent-type definitions as teammates — Feature A wiring
# --------------------------------------------------------------------------- #

def test_spawn_teammate_from_agent_definition(teams_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    (tmp_path / "rev.md").write_text(
        "---\nname: reviewer\ndescription: reviews code\n"
        "toolsets: [read_file]\nmodel: claude-haiku-4-5\nprovider: anthropic\n---\n"
        "You are a strict security reviewer."
    )
    tid = coordinator.create_team("t")
    calls = {}

    def fake_dispatch(prompt, **kw):
        calls.update(kw)
        calls["prompt"] = prompt
        return "bg9"

    bg = coordinator.spawn_teammate(
        tid, "alice", "review the auth module", agent_type="reviewer", dispatch_fn=fake_dispatch
    )
    assert bg == "bg9"
    assert calls["model"] == "claude-haiku-4-5"
    assert calls["provider"] == "anthropic"
    assert calls["toolsets"] == ["read_file"]
    # The definition's persona AND the concrete assignment both reach the teammate.
    assert "strict security reviewer" in calls["prompt"]
    assert "review the auth module" in calls["prompt"]
    # Role defaults to the definition name.
    assert db.get_member(tid, "alice")["role"] == "reviewer"


def test_spawn_teammate_unknown_agent_type_raises(teams_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    tid = coordinator.create_team("t")
    with pytest.raises(ValueError):
        coordinator.spawn_teammate(
            tid, "alice", "x", agent_type="ghost", dispatch_fn=lambda *a, **k: "b"
        )
    # A failed resolve must not half-register the member.
    assert db.get_member(tid, "alice") is None


def test_spawn_teammate_explicit_model_overrides_definition(teams_db, tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    (tmp_path / "rev.md").write_text("---\nname: reviewer\nmodel: def-model\n---\nbody")
    tid = coordinator.create_team("t")
    calls = {}
    coordinator.spawn_teammate(
        tid, "alice", "x", agent_type="reviewer", model="explicit-model",
        dispatch_fn=lambda p, **k: calls.update(k) or "b",
    )
    assert calls["model"] == "explicit-model"  # explicit arg wins over the definition


def test_cli_spawn_passes_agent_type(teams_db, monkeypatch, capsys):
    parser = _make_parser()
    cli.handle(parser.parse_args(["team", "create", "alpha"]))
    tid = capsys.readouterr().out.split()[1].rstrip(":")
    captured = {}
    monkeypatch.setattr(coordinator, "spawn_teammate", lambda *a, **k: captured.update(k) or "bgX")
    rc = cli.handle(parser.parse_args(["team", "spawn", tid, "alice", "do it", "--agent", "reviewer"]))
    assert rc == 0
    assert captured["agent_type"] == "reviewer"


def test_cli_defs_lists_agent_definitions(teams_db, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))
    (tmp_path / "r.md").write_text("---\nname: reviewer\ndescription: reviews code\n---\nbody")
    parser = _make_parser()
    rc = cli.handle(parser.parse_args(["team", "defs"]))
    assert rc == 0
    out = capsys.readouterr().out
    assert "reviewer" in out and "reviews code" in out
