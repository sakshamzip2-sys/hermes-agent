"""Tests for the reusable agent-type definition loader (tools.agent_defs).

Stdlib + pytest only. An agent definition is a Markdown file with YAML
frontmatter (mirroring Claude-Code's .claude/agents/*.md) whose body is the
agent's system prompt. The loader is a plain module — never a model tool — so
it can be consumed by delegate_task (core), oc_teams spawn, and oc_agents
dispatch without enlarging the always-on tool schema.
"""

from __future__ import annotations

from tools import agent_defs


FULL_DEF = """---
name: code-reviewer
description: Reviews code for bugs and security issues. Use proactively after edits.
tools: [read_file, web]
model: claude-haiku-4-5
provider: anthropic
permissionMode: plan
effort: high
maxTurns: 12
memory: project
skills: [security-review]
---
You are a meticulous code reviewer.
Report findings as Critical / Warning / Suggestion.
"""


def test_parse_definition_all_fields():
    d = agent_defs.parse_agent_definition(FULL_DEF)
    assert d is not None
    assert d.name == "code-reviewer"
    assert "Reviews code" in d.description
    assert d.toolsets == ["read_file", "web"]
    assert d.model == "claude-haiku-4-5"
    assert d.provider == "anthropic"
    assert d.permission_mode == "plan"
    assert d.effort == "high"
    assert d.max_iterations == 12
    assert d.memory == "project"
    assert d.skills == ["security-review"]
    assert "meticulous code reviewer" in d.prompt
    assert "Critical / Warning / Suggestion" in d.prompt


def test_parse_requires_name():
    assert agent_defs.parse_agent_definition("---\ndescription: x\n---\nbody") is None


def test_parse_non_frontmatter_returns_none():
    assert agent_defs.parse_agent_definition("just a plain file, no frontmatter") is None


def test_tools_accepts_comma_string():
    d = agent_defs.parse_agent_definition("---\nname: x\ntools: read_file, web\n---\nbody")
    assert d.toolsets == ["read_file", "web"]


def test_toolsets_alias_accepted():
    d = agent_defs.parse_agent_definition("---\nname: x\ntoolsets: [a, b]\n---\nbody")
    assert d.toolsets == ["a", "b"]


def test_bad_max_turns_is_ignored_not_fatal():
    d = agent_defs.parse_agent_definition("---\nname: x\nmaxTurns: not-a-number\n---\nbody")
    assert d is not None and d.max_iterations is None


def test_name_is_normalized_lowercase():
    d = agent_defs.parse_agent_definition("---\nname: Code-Reviewer\n---\nbody")
    assert d.name == "code-reviewer"


def test_load_and_project_overrides_user(tmp_path):
    proj = tmp_path / "proj"
    user = tmp_path / "user"
    proj.mkdir()
    user.mkdir()
    (user / "a.md").write_text("---\nname: shared\nmodel: user-model\n---\nuser body")
    (proj / "a.md").write_text("---\nname: shared\nmodel: proj-model\n---\nproj body")
    (user / "b.md").write_text("---\nname: only-user\n---\nbody")

    # Most-specific dir first; first occurrence of a name wins.
    defs = agent_defs.load_agent_definitions([proj, user])
    assert defs["shared"].model == "proj-model"
    assert "only-user" in defs


def test_get_by_name_case_insensitive(tmp_path):
    (tmp_path / "c.md").write_text("---\nname: Code-Reviewer\n---\nbody")
    d = agent_defs.get_agent_definition("code-reviewer", dirs=[tmp_path])
    assert d is not None and d.name == "code-reviewer"


def test_unknown_name_returns_none(tmp_path):
    assert agent_defs.get_agent_definition("nope", dirs=[tmp_path]) is None


def test_missing_dir_is_safe(tmp_path):
    # Non-existent dirs must not raise.
    defs = agent_defs.load_agent_definitions([tmp_path / "does-not-exist"])
    assert defs == {}
