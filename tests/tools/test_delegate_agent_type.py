#!/usr/bin/env python3
"""Tests for agent-type definitions + per-call model in delegate_task (Feature A).

Covers the two pure helpers (no AIAgent construction) plus the user-facing
rejection of an unknown agent_type. End-to-end child construction is exercised
by the existing test_delegate.py suite.
"""

import json
import threading
from unittest.mock import MagicMock

from tools.delegate_tool import (
    _effective_task_fields,
    _task_runtime_override,
    delegate_task,
)
from tools.agent_defs import AgentDefinition


def _mock_parent():
    parent = MagicMock()
    parent._delegate_depth = 0
    parent._active_children = []
    parent._active_children_lock = threading.Lock()
    return parent


# --------------------------------------------------------------------------- #
# _task_runtime_override — per-call model/provider, model-agnostic
# --------------------------------------------------------------------------- #

BASE = {"model": "parent-model", "provider": None, "base_url": None,
        "api_key": None, "api_mode": None}


def test_runtime_override_noop_when_nothing_requested():
    assert _task_runtime_override(BASE, model=None, provider=None) is BASE


def test_runtime_override_model_only_keeps_endpoint():
    out = _task_runtime_override(BASE, model="claude-haiku-4-5", provider=None)
    assert out["model"] == "claude-haiku-4-5"
    # Same endpoint as the parent (only the model swapped).
    assert out["provider"] == BASE["provider"]
    assert out["base_url"] == BASE["base_url"]


def test_runtime_override_provider_reresolves(monkeypatch):
    captured = {}

    def fake_resolve(*, requested=None, target_model=None, **kw):
        captured["requested"] = requested
        captured["target_model"] = target_model
        return {
            "provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
            "api_key": "sk-x", "api_mode": "chat_completions", "model": target_model,
        }

    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider", fake_resolve
    )
    out = _task_runtime_override(BASE, model="some/model", provider="openrouter")
    assert captured == {"requested": "openrouter", "target_model": "some/model"}
    assert out["provider"] == "openrouter"
    assert out["base_url"] == "https://openrouter.ai/api/v1"
    assert out["api_key"] == "sk-x"
    assert out["model"] == "some/model"


# --------------------------------------------------------------------------- #
# _effective_task_fields — merge an agent-type definition into a task
# --------------------------------------------------------------------------- #

def test_effective_fields_without_definition_passthrough():
    task = {"goal": "g", "context": "ctx", "toolsets": ["web"]}
    ctx, toolsets, model, provider = _effective_task_fields(task, ["file"], None)
    assert ctx == "ctx" and toolsets == ["web"] and model is None and provider is None


def test_effective_fields_merges_definition():
    d = AgentDefinition(
        name="reviewer", prompt="You are a reviewer.",
        toolsets=["read_file"], model="def-model", provider="anthropic",
    )
    task = {"goal": "review it"}
    ctx, toolsets, model, provider = _effective_task_fields(task, ["file"], d)
    assert "You are a reviewer." in ctx           # persona folded into context
    assert toolsets == ["read_file"]              # def toolsets used (task gave none)
    assert model == "def-model" and provider == "anthropic"


def test_effective_fields_task_overrides_definition():
    d = AgentDefinition(name="reviewer", toolsets=["read_file"], model="def-model")
    task = {"goal": "g", "context": "mine", "toolsets": ["web"], "model": "task-model"}
    ctx, toolsets, model, provider = _effective_task_fields(task, ["file"], d)
    assert toolsets == ["web"]      # explicit task toolsets win
    assert model == "task-model"    # explicit task model wins
    assert "mine" in ctx


# --------------------------------------------------------------------------- #
# Integration: unknown agent_type is rejected before any child is built
# --------------------------------------------------------------------------- #

def test_unknown_agent_type_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_AGENTS_DIR", str(tmp_path))  # empty -> no definitions
    result = json.loads(
        delegate_task(tasks=[{"goal": "x", "agent_type": "ghost"}], parent_agent=_mock_parent())
    )
    assert "error" in result and "ghost" in result["error"]
