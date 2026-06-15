"""Regression: the custom provider must NOT send ``reasoning_effort`` to Claude
Haiku.

The OpenComputer router advertises Haiku as reasoning-capable but rejects the
OpenAI-style ``reasoning_effort`` parameter for it with HTTP 400 ("This model
does not support the effort parameter"). When a reasoning effort is set (the
default), that silently failed every Haiku request. ``build_api_kwargs_extras``
now excludes Haiku from the reasoning branch so it answers normally.
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "custom_provider_under_test",
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "model-providers"
    / "custom"
    / "__init__.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)
custom = _MOD.custom

_REASONING = {"effort": "high", "enabled": True}


def test_haiku_gets_no_reasoning_effort():
    _, top_level = custom.build_api_kwargs_extras(
        reasoning_config=_REASONING,
        supports_reasoning=True,
        model="claude-haiku-4-5",
    )
    assert "reasoning_effort" not in top_level
    # Haiku never enters the thinking branch, so its temperature is left to the
    # caller (the temperature==1 override is only for thinking-enabled Claude).
    assert "temperature" not in top_level


def test_opus_and_sonnet_still_get_reasoning_effort():
    for model in ("claude-opus-4-6", "claude-sonnet-4-6"):
        _, top_level = custom.build_api_kwargs_extras(
            reasoning_config=_REASONING,
            supports_reasoning=True,
            model=model,
        )
        assert top_level.get("reasoning_effort") == "high", model
        assert top_level.get("temperature") == 1, model


def test_haiku_case_insensitive_and_pathy_names():
    for model in ("anthropic/Claude-Haiku-4-5", "router/CLAUDE-HAIKU"):
        _, top_level = custom.build_api_kwargs_extras(
            reasoning_config=_REASONING,
            supports_reasoning=True,
            model=model,
        )
        assert "reasoning_effort" not in top_level, model
