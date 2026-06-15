"""Tests for the skill_run tool (frontmatter-driven skill execution).

skill_run ports v1's SkillTool execution semantics: ``context: fork`` delegates
to an isolated subagent; ``context: inline`` (default) returns the skill body.
"""

import json

import tools.skill_run_tool as srt
from tools.registry import registry


def test_skill_run_is_registered_in_skills_toolset():
    entry = registry.get_entry("skill_run")
    assert entry is not None
    assert entry.toolset == "skills"
    assert "skill_run" in registry.get_tool_names_for_toolset("skills")


def test_inline_returns_body(monkeypatch):
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "demo", "context": "inline"}, "INLINE BODY"),
    )
    result = json.loads(srt.skill_run("demo"))
    assert result["success"] is True
    assert result["context"] == "inline"
    assert result["content"] == "INLINE BODY"


def test_default_context_is_inline(monkeypatch):
    # No explicit context → treated as inline.
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "demo"}, "BODY"),
    )
    result = json.loads(srt.skill_run("demo"))
    assert result["success"] is True
    assert result["context"] == "inline"


def test_fork_delegates_with_body_and_toolsets(monkeypatch):
    captured = {}

    def fake_delegate(goal=None, toolsets=None, parent_agent=None, **kw):
        captured["goal"] = goal
        captured["toolsets"] = toolsets
        captured["parent_agent"] = parent_agent
        # delegate_task returns a JSON-encoded string in reality.
        return json.dumps({"results": [{"status": "completed", "summary": "OK"}]})

    import tools.delegate_tool as dt
    monkeypatch.setattr(dt, "delegate_task", fake_delegate)
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: (
            {"name": "forky", "context": "fork", "toolsets": ["web", "browser"]},
            "DO THE THING",
        ),
    )

    result = json.loads(srt.skill_run("forky", context="extra ctx", parent_agent="PA"))
    assert result["success"] is True
    assert result["context"] == "fork"
    # BUG1 fix: result is a parsed object, NOT a doubly-encoded string.
    assert isinstance(result["result"], dict)
    assert result["result"]["results"][0]["summary"] == "OK"
    # The skill body and caller context flow into the subagent goal.
    assert "DO THE THING" in captured["goal"]
    assert "extra ctx" in captured["goal"]
    # Only v2-native `toolsets:` (group names) are forwarded.
    assert captured["toolsets"] == ["web", "browser"]
    assert captured["parent_agent"] == "PA"


def test_fork_requires_parent_agent(monkeypatch):
    # BUG2 fix: no parent_agent → clear failure, not masked success.
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "f", "context": "fork"}, "BODY"),
    )
    result = json.loads(srt.skill_run("f"))  # parent_agent defaults to None
    assert result["success"] is False
    assert "agent context" in result["error"]


def test_fork_surfaces_delegate_failure(monkeypatch):
    # BUG2 fix: delegate_task RETURNS an error JSON → surfaced as failure.
    import tools.delegate_tool as dt
    monkeypatch.setattr(
        dt, "delegate_task",
        lambda **kw: json.dumps({"success": False, "error": "child blew up"}),
    )
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "f", "context": "fork"}, "BODY"),
    )
    result = json.loads(srt.skill_run("f", parent_agent="PA"))
    assert result["success"] is False
    assert "child blew up" in result["error"]


def test_v1_tools_list_is_not_forwarded_as_toolsets(monkeypatch):
    # v1 `tools:` are individual tool names that delegate_task would drop —
    # they must NOT be passed as toolset group names.
    captured = {}
    import tools.delegate_tool as dt
    monkeypatch.setattr(
        dt, "delegate_task",
        lambda toolsets=None, **kw: captured.update(toolsets=toolsets) or "{}",
    )
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "f", "context": "fork", "tools": ["web_search"]}, "B"),
    )
    json.loads(srt.skill_run("f", parent_agent="PA"))
    assert captured["toolsets"] is None


def test_fork_surfaces_model_override_as_note(monkeypatch):
    captured = {}
    import tools.delegate_tool as dt
    monkeypatch.setattr(
        dt, "delegate_task",
        lambda goal=None, **kw: captured.update(goal=goal) or "{}",
    )
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "m", "context": "fork", "model": "grok-4"}, "BODY"),
    )
    json.loads(srt.skill_run("m", parent_agent="PA"))
    assert "grok-4" in captured["goal"]  # documented, not silently dropped


def test_qualified_plugin_name_rejected():
    result = json.loads(srt.skill_run("superpowers:writing-plans"))
    assert result["success"] is False
    assert "qualified plugin skills" in result["error"]


def test_disabled_skill_rejected(monkeypatch):
    monkeypatch.setattr(srt, "_resolve_skill", lambda name: (None, "__disabled__"))
    result = json.loads(srt.skill_run("x"))
    assert result["success"] is False
    assert "disabled" in result["error"]


def test_platform_mismatch_rejected(monkeypatch):
    monkeypatch.setattr(srt, "_resolve_skill", lambda name: (None, "__platform__"))
    result = json.loads(srt.skill_run("x"))
    assert result["success"] is False
    assert "platform" in result["error"]


def test_unknown_context_is_rejected(monkeypatch):
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "bad", "context": "weird"}, "x"),
    )
    result = json.loads(srt.skill_run("bad"))
    assert result["success"] is False
    assert "unknown context" in result["error"]


def test_missing_skill_is_rejected(monkeypatch):
    monkeypatch.setattr(srt, "_resolve_skill", lambda name: (None, None))
    result = json.loads(srt.skill_run("nope"))
    assert result["success"] is False
    assert "not found" in result["error"]


def test_empty_name_is_rejected():
    result = json.loads(srt.skill_run(""))
    assert result["success"] is False
    assert "name is required" in result["error"]


def test_fork_failure_is_handled(monkeypatch):
    import tools.delegate_tool as dt

    def boom(**kw):
        raise RuntimeError("spawn blew up")

    monkeypatch.setattr(dt, "delegate_task", boom)
    monkeypatch.setattr(
        srt, "_resolve_skill",
        lambda name: ({"name": "f", "context": "fork"}, "BODY"),
    )
    result = json.loads(srt.skill_run("f", parent_agent="PA"))
    assert result["success"] is False
    assert "fork failed" in result["error"]
