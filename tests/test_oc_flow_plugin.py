"""Tests for the oc_flow dynamic-workflows plugin.

Stdlib + pytest only, no network, no LLM. Every test runs against an isolated
SQLite DB (``HERMES_OC_FLOW_DB`` → tmp) and drives the engine with an injected
fake agent runner, so the full machinery — phases, parallel/pipeline fan-out,
the resume cache, structured output, and the CLI — is exercised deterministically.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List

import pytest

from plugins.oc_flow import db
from plugins.oc_flow.executor import (
    AgentResult,
    AgentSpec,
    fake_agent_runner,
    resolve_default_runner,
    run_agent_task,
)
from plugins.oc_flow.runtime import extract_meta, run_flow


@pytest.fixture()
def flow_db(tmp_path, monkeypatch):
    """Isolate the oc_flow DB to a temp file and reset the cached connection."""
    monkeypatch.setenv("HERMES_OC_FLOW_DB", str(tmp_path / "oc_flow.db"))
    # Drop any thread-local connection so connect() reopens against the tmp path.
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
# meta extraction
# --------------------------------------------------------------------------- #

def test_extract_meta_reads_literal():
    src = 'META = {"name": "x", "description": "d", "phases": ["A", "B"]}\nphase("A")\n'
    meta = extract_meta(src)
    assert meta["name"] == "x"
    assert meta["phases"] == ["A", "B"]


def test_extract_meta_handles_missing_and_nonliteral():
    assert extract_meta("x = 1\n") == {}
    # A computed META must NOT be executed — literal_eval fails → {}.
    assert extract_meta("META = dict(name='x')\n") == {}


# --------------------------------------------------------------------------- #
# DB layer
# --------------------------------------------------------------------------- #

def test_db_run_lifecycle(flow_db):
    rid = db.new_run_id()
    db.create_run(run_id=rid, name="t", description="d", script_path="s.py")
    run = db.get_run(rid)
    assert run is not None and run["status"] == "pending"

    db.mark_run_started(rid, pid=123)
    assert db.get_run(rid)["status"] == "running"

    db.add_phase(rid, 1, "Review")
    db.add_phase(rid, 1, "Review")  # idempotent on (run_id, seq)
    assert len(db.list_phases(rid)) == 1

    db.finish_run(rid, "completed", result={"ok": True})
    run = db.get_run(rid)
    assert run["status"] == "completed"
    assert db.decode_result(run) == {"ok": True}

    assert any(r["id"] == rid for r in db.list_runs())


def test_db_agent_resume_cache(flow_db):
    rid = db.new_run_id()
    db.create_run(run_id=rid, name="t")
    db.start_agent(rid, 1, label="a", prompt_sha="sha1")
    db.finish_agent(rid, 1, status="completed", result="hello", api_calls=2)

    # Cache hit only when the prompt hash matches.
    cached = db.get_cached_agent(rid, 1, "sha1")
    assert cached is not None and db.decode_result(cached) == "hello"
    assert db.get_cached_agent(rid, 1, "DIFFERENT") is None
    # A still-running agent is never a cache hit.
    db.start_agent(rid, 2, label="b", prompt_sha="sha2")
    assert db.get_cached_agent(rid, 2, "sha2") is None


# --------------------------------------------------------------------------- #
# Engine — sequential, parallel, pipeline
# --------------------------------------------------------------------------- #

def _echo_runner(spec: AgentSpec) -> AgentResult:
    return AgentResult(text=f"echo:{spec.prompt}", ok=True, api_calls=1, model="fake")


def test_sequential_flow(flow_db):
    src = """
META = {"name": "seq"}
phase("work")
log("starting")
a = agent("first")
b = agent("second")
result({"a": a, "b": b})
"""
    out = run_flow(source=src, agent_runner=_echo_runner)
    assert out.status == "completed", out.error
    assert out.result == {"a": "echo:first", "b": "echo:second"}
    assert out.agent_count == 2
    agents = db.list_agents(out.run_id)
    assert [a["status"] for a in agents] == ["completed", "completed"]
    assert {log["message"] for log in db.list_logs(out.run_id)} >= {"starting"}


def test_parallel_fanout(flow_db):
    src = """
results = parallel([(lambda i=i: agent(f"task {i}")) for i in range(5)])
result(results)
"""
    out = run_flow(source=src, agent_runner=_echo_runner)
    assert out.status == "completed", out.error
    assert out.result == [f"echo:task {i}" for i in range(5)]
    assert out.agent_count == 5


def test_pipeline_stages_chain(flow_db):
    src = """
items = ["x", "y"]
out = pipeline(items, lambda it: agent(f"stage1 {it}"), lambda prev: agent(f"stage2 {prev}"))
result(out)
"""
    out = run_flow(source=src, agent_runner=_echo_runner)
    assert out.status == "completed", out.error
    # Each item flows stage1 -> stage2 independently.
    assert out.result == ["echo:stage2 echo:stage1 x", "echo:stage2 echo:stage1 y"]
    assert out.agent_count == 4


def test_pipeline_stage_receives_item_and_index(flow_db):
    src = """
def stage2(prev, item, index):
    return f"{item}#{index}:{prev}"
out = pipeline(["a", "b"], lambda it: agent(it), stage2)
result(out)
"""
    out = run_flow(source=src, agent_runner=_echo_runner)
    assert out.status == "completed", out.error
    assert out.result == ["a#0:echo:a", "b#1:echo:b"]


# --------------------------------------------------------------------------- #
# Structured output
# --------------------------------------------------------------------------- #

def test_structured_schema_via_fake_runner(flow_db):
    src = """
SCHEMA = {"type": "object", "required": ["findings"],
          "properties": {"findings": {"type": "array"}}}
r = agent("find things", schema=SCHEMA)
result(r)
"""
    out = run_flow(source=src, agent_runner=fake_agent_runner)
    assert out.status == "completed", out.error
    assert out.result == {"findings": []}


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #

def test_flow_that_raises_is_marked_failed(flow_db):
    src = "raise ValueError('boom')\n"
    out = run_flow(source=src, agent_runner=_echo_runner)
    assert out.status == "failed"
    assert "boom" in (out.error or "")
    assert db.get_run(out.run_id)["status"] == "failed"


def test_failed_agent_propagates(flow_db):
    def failing(spec: AgentSpec) -> AgentResult:
        return AgentResult(ok=False, error="model exploded")

    src = "result(agent('go'))\n"
    out = run_flow(source=src, agent_runner=failing)
    assert out.status == "failed"
    agents = db.list_agents(out.run_id)
    assert agents and agents[0]["status"] == "failed"


# --------------------------------------------------------------------------- #
# Resume — the cache key is content-addressed, so it survives concurrency
# --------------------------------------------------------------------------- #

def test_resume_skips_cached_agents(flow_db):
    calls = {"n": 0}
    lock = threading.Lock()

    def counting(spec: AgentSpec) -> AgentResult:
        with lock:
            calls["n"] += 1
        return AgentResult(text=f"r:{spec.prompt}", ok=True, api_calls=1)

    src = """
phase("p")
a = parallel([(lambda i=i: agent(f"task {i}")) for i in range(4)])
result(a)
"""
    out1 = run_flow(source=src, agent_runner=counting)
    assert out1.status == "completed"
    assert calls["n"] == 4
    first_result = out1.result

    # Resume the SAME run with the SAME script: every agent is a cache hit.
    out2 = run_flow(source=src, run_id=out1.run_id, resume=True, agent_runner=counting)
    assert out2.status == "completed", out2.error
    assert calls["n"] == 4  # no new runner invocations
    assert out2.result == first_result


def test_resume_runs_only_new_agents(flow_db):
    calls: List[str] = []
    lock = threading.Lock()

    def recording(spec: AgentSpec) -> AgentResult:
        with lock:
            calls.append(spec.prompt)
        return AgentResult(text=f"r:{spec.prompt}", ok=True)

    src1 = "result([agent('alpha'), agent('beta')])\n"
    out1 = run_flow(source=src1, agent_runner=recording)
    assert out1.status == "completed"
    assert calls == ["alpha", "beta"]

    # Edited script: 'alpha' cached, 'gamma' is new.
    src2 = "result([agent('alpha'), agent('gamma')])\n"
    out2 = run_flow(source=src2, run_id=out1.run_id, resume=True, agent_runner=recording)
    assert out2.status == "completed", out2.error
    assert "gamma" in calls
    assert calls.count("alpha") == 1  # alpha was NOT re-run
    assert out2.result == ["r:alpha", "r:gamma"]


# --------------------------------------------------------------------------- #
# Runner selection
# --------------------------------------------------------------------------- #

def test_parallel_is_actually_concurrent(flow_db):
    """Prove parallel() truly overlaps work and respects the cap — a purely
    sequential implementation would fail this (max overlap would be 1)."""
    import time

    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def slow(spec: AgentSpec) -> AgentResult:
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.12)
        with lock:
            active["now"] -= 1
        return AgentResult(text="ok", ok=True)

    src = "result(parallel([(lambda i=i: agent(f't{i}')) for i in range(6)]))\n"
    out = run_flow(source=src, agent_runner=slow, max_concurrency=4)
    assert out.status == "completed", out.error
    assert active["max"] >= 2, "parallel() did not run concurrently"
    assert active["max"] <= 4, "parallel() exceeded the concurrency cap"


def test_cache_key_includes_cwd_and_max_iterations():
    from plugins.oc_flow.runtime import _sha

    base = _sha(AgentSpec(prompt="p"))
    with_cwd = _sha(AgentSpec(prompt="p", extra={"cwd": "/tmp/wt1"}))
    with_iter = _sha(AgentSpec(prompt="p", max_iterations=99))
    # Specs that differ only by cwd or max_iterations must hash differently,
    # or resume could serve one agent's result to a materially different one.
    assert len({base, with_cwd, with_iter}) == 3


def test_resume_serves_latest_value_per_spec(flow_db):
    """Across edits, a re-introduced identical spec resumes to its NEWEST
    cached value, not an arbitrarily old one (the dedup-to-latest fix)."""
    counter = {"n": 0}
    lock = threading.Lock()

    def varying(spec: AgentSpec) -> AgentResult:
        with lock:
            counter["n"] += 1
            n = counter["n"]
        return AgentResult(text=f"v{n}", ok=True)

    # run1: same spec called twice -> two completed rows (v1, v2), same sha.
    src = "result([agent('dup'), agent('dup')])\n"
    out1 = run_flow(source=src, agent_runner=varying)
    assert out1.result == ["v1", "v2"]

    # resume: both calls are cache hits and both get the latest value (v2);
    # the runner is never invoked again.
    out2 = run_flow(source=src, run_id=out1.run_id, resume=True, agent_runner=varying)
    assert counter["n"] == 2
    assert out2.result == ["v2", "v2"]


def test_worktree_isolation_for_subagent(flow_db, tmp_path):
    """agent(worktree=True) runs the subagent in its own git worktree, and the
    worktree path (not the user cwd) is what the runner receives — while the
    resume key stays stable (hashed on the logical spec, not the worktree)."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "f.txt").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)

    seen_cwds = []

    def capture_cwd(spec: AgentSpec) -> AgentResult:
        seen_cwds.append((spec.extra or {}).get("cwd"))
        return AgentResult(text="ok", ok=True)

    src = f"result(agent('edit a file', cwd={str(repo)!r}, worktree=True))\n"
    out = run_flow(source=src, agent_runner=capture_cwd)
    assert out.status == "completed", out.error
    # The runner saw a worktree path under .worktrees/, not the bare repo root.
    cwd = seen_cwds[0]
    assert cwd is not None and ".worktrees/hermes-flow-" in cwd
    # Unchanged worktree was auto-removed.
    assert not Path(cwd).exists()


def test_worktree_helpers(tmp_path):
    import subprocess

    from plugins.oc_flow import worktrees

    # Outside a git repo: graceful None.
    assert worktrees.create_worktree(str(tmp_path)) is None

    repo = tmp_path / "r"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "a").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=repo, check=True, capture_output=True)

    wt = worktrees.create_worktree(str(repo))
    assert wt and Path(wt["path"]).is_dir()
    assert not worktrees.worktree_has_changes(wt["path"])
    # A new file makes it dirty → cleanup keeps it.
    (Path(wt["path"]) / "new.txt").write_text("dirty")
    assert worktrees.worktree_has_changes(wt["path"]) is True
    assert worktrees.cleanup_if_unchanged(wt) is False
    assert Path(wt["path"]).exists()
    # Force remove.
    assert worktrees.remove_worktree(wt["path"], force=True) is True


def test_worktreeinclude_copies_gitignored_files(tmp_path):
    """A gitignored file listed in .worktreeinclude is copied into a new worktree
    (so a subagent gets e.g. its .env), while a non-listed gitignored file is not."""
    import subprocess

    from plugins.oc_flow import worktrees

    repo = tmp_path / "r"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t.t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / ".gitignore").write_text(".env\nsecret.txt\n")
    (repo / ".worktreeinclude").write_text(".env\n")  # only .env is opted in
    (repo / ".env").write_text("TOKEN=abc")
    (repo / "secret.txt").write_text("nope")
    (repo / "tracked.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)

    wt = worktrees.create_worktree(str(repo))
    assert wt is not None
    wt_path = Path(wt["path"])
    # .env was copied (listed + gitignored); secret.txt was NOT (gitignored but not listed).
    assert (wt_path / ".env").read_text() == "TOKEN=abc"
    assert not (wt_path / "secret.txt").exists()
    worktrees.remove_worktree(str(wt_path), force=True)


def test_resolve_default_runner_env(monkeypatch):
    monkeypatch.delenv("OC_FLOW_FAKE_AGENT", raising=False)
    assert resolve_default_runner() is run_agent_task
    monkeypatch.setenv("OC_FLOW_FAKE_AGENT", "1")
    assert resolve_default_runner() is fake_agent_runner


def test_fake_runner_stub_shapes():
    schema = {
        "type": "object",
        "required": ["title", "count", "ok", "tags"],
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer"},
            "ok": {"type": "boolean"},
            "tags": {"type": "array"},
        },
    }
    res = fake_agent_runner(AgentSpec(prompt="x", schema=schema))
    assert res.ok
    assert res.structured == {"title": "fake", "count": 0, "ok": False, "tags": []}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_list_and_show(flow_db, capsys):
    import argparse

    from plugins.oc_flow import cli

    # Seed a completed run via the engine.
    out = run_flow(source='result(agent("hi"))\n', agent_runner=_echo_runner)

    # Mirror how main.py wires a plugin CLI command: setup() receives the
    # `flow` subparser node and adds its own sub-subcommands.
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    flow_parser = subparsers.add_parser("flow")
    cli.setup(flow_parser)

    args = parser.parse_args(["flow", "list"])
    assert cli.handle(args) == 0
    listing = capsys.readouterr().out
    assert out.run_id in listing

    args = parser.parse_args(["flow", "show", out.run_id])
    assert cli.handle(args) == 0
    shown = capsys.readouterr().out
    assert "completed" in shown
    assert "echo:hi" in shown


def test_cli_show_unknown_run(flow_db, capsys):
    import argparse

    from plugins.oc_flow import cli

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    flow_parser = subparsers.add_parser("flow")
    cli.setup(flow_parser)
    args = parser.parse_args(["flow", "show", "flow_doesnotexist"])
    assert cli.handle(args) == 2
