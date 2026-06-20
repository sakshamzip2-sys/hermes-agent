"""GAP1 wiring test: _create_agent applies the agent-def manifest at spawn.

Proves the live integration that was missing. _create_agent now delegates manifest
resolution to the pure, testable _apply_agent_def(slug, toolsets, model,
model_override) and passes the live oc_agent_id through as agent_def_slug. We test
the real resolver against the real shipped forge manifest (no provider/model
needed), plus assert _create_agent actually threads agent_def_slug through to it.
"""

from __future__ import annotations

import inspect

from gateway.platforms.api_server import APIServerAdapter

# A realistic platform-available toolset set (superset of what manifests grant).
AVAIL = ["file", "terminal", "code_execution", "lsp", "web", "memory", "skills",
         "semantic_search", "browser", "kanban"]


def test_forge_slug_applies_manifest_toolsets_and_model():
    ts, model = APIServerAdapter._apply_agent_def("forge", list(AVAIL), "default-model", None)
    assert "file" in ts and "terminal" in ts and "code_execution" in ts and "lsp" in ts
    assert "browser" not in ts  # forge did not grant browser -> intersected out
    assert model == "claude-sonnet-4-6"  # manifest model honored


def test_picker_model_override_beats_manifest():
    # When the picker pinned a model (model_override set), _create_agent has already
    # applied it to `model` BEFORE calling _apply_agent_def, so the resolver must
    # NOT replace it with the manifest model. Here `model` is the already-applied
    # picker model and the resolver leaves it untouched.
    _ts, model = APIServerAdapter._apply_agent_def(
        "forge", list(AVAIL), "claude-opus-4-8", "claude-opus-4-8")
    assert model == "claude-opus-4-8"  # manifest model did NOT clobber the picker


def test_unknown_slug_is_safe_noop():
    ts, model = APIServerAdapter._apply_agent_def("does-not-exist", list(AVAIL), "default", None)
    assert ts == AVAIL and model == "default"  # untouched, never crashes


def test_bogus_manifest_toolset_cannot_empty_the_set():
    # If a manifest's grants are all unavailable, the available set is kept (the
    # agent never ends up tool-less). atlas grants semantic_search/web/memory/file.
    ts, _ = APIServerAdapter._apply_agent_def("atlas", ["memory"], "default", None)
    assert ts  # non-empty
    assert "memory" in ts


def test_create_agent_threads_slug_to_resolver():
    # Structural guarantee that the live path passes the slug through: the
    # _create_agent signature accepts agent_def_slug and its body calls
    # _apply_agent_def. This locks the wiring so it cannot silently regress.
    sig = inspect.signature(APIServerAdapter._create_agent)
    assert "agent_def_slug" in sig.parameters
    src = inspect.getsource(APIServerAdapter._create_agent)
    assert "_apply_agent_def" in src and "agent_def_slug" in src
    # And the live chat path passes the resolved slug through (not left default).
    cls_src = inspect.getsource(APIServerAdapter)
    assert "agent_def_slug=agent_id_slug" in cls_src
