"""Tests for Tool Use Examples — Anthropic's "advanced tool use" feature made
model-agnostic in OC.

A tool opts in by adding an ``input_examples`` list to its schema dict. At
serialization time (registry.get_definitions) those examples are folded into the
tool ``description`` (so every provider — Anthropic, OpenAI, OpenRouter, the OC
Router — forwards them to the model) and the raw ``input_examples`` key is
stripped from the emitted OpenAI ``function`` object.
"""

from tools.registry import ToolRegistry, _augment_description_with_examples


def _plain_params():
    return {"type": "object", "properties": {}, "required": []}


def test_examples_render_into_description_and_raw_key_stripped():
    reg = ToolRegistry()
    schema = {
        "name": "demo",
        "description": "Demo tool.",
        "parameters": _plain_params(),
        "input_examples": [{"x": 1}, {"y": "two"}],
    }
    reg.register(name="demo", toolset="test", schema=schema, handler=lambda **k: "ok")

    defs = reg.get_definitions({"demo"})
    assert len(defs) == 1
    fn = defs[0]["function"]

    # Raw key never reaches the provider (strict OpenAI-compat validators 400 on it).
    assert "input_examples" not in fn
    # Examples are visible to the model via the description.
    assert "Example calls" in fn["description"]
    assert '{"x": 1}' in fn["description"]
    assert '{"y": "two"}' in fn["description"]
    # Original description preserved as the prefix.
    assert fn["description"].startswith("Demo tool.")
    # The stored schema dict is NOT mutated (get_definitions works on a copy).
    assert "input_examples" in schema


def test_tool_without_examples_is_unchanged():
    reg = ToolRegistry()
    schema = {
        "name": "plain",
        "description": "Plain tool.",
        "parameters": _plain_params(),
    }
    reg.register(name="plain", toolset="test", schema=schema, handler=lambda **k: "ok")

    fn = reg.get_definitions({"plain"})[0]["function"]
    assert "Example calls" not in fn["description"]
    assert fn["description"] == "Plain tool."


def test_augment_helper_edge_cases():
    # Normal case: appends a block after the base description.
    out = _augment_description_with_examples("Base.", [{"a": 1}])
    assert out.startswith("Base.")
    assert "Example calls" in out and '{"a": 1}' in out

    # Empty / non-list examples leave the description untouched.
    assert _augment_description_with_examples("Base.", []) == "Base."
    assert _augment_description_with_examples("Base.", None) == "Base."
    assert _augment_description_with_examples("Base.", "nope") == "Base."

    # Empty base description: block becomes the whole description (no leading newlines).
    out2 = _augment_description_with_examples("", [{"a": 1}])
    assert out2.startswith("Example calls")


def test_shipped_tools_populate_examples():
    """The tools we wired examples onto actually declare + render them."""
    import tools.todo_tool  # noqa: F401  (registers "todo")
    import tools.clarify_tool  # noqa: F401  (registers "clarify")
    import tools.cronjob_tools  # noqa: F401  (registers "cronjob")
    from tools.registry import registry

    # All three are registered and declare input_examples in their schema.
    for name in ("todo", "clarify", "cronjob"):
        entry = registry.get_entry(name)
        assert entry is not None, f"{name} not registered"
        assert entry.schema.get("input_examples"), f"{name} missing input_examples"

    # And get_definitions renders them for tools available in this env (some,
    # e.g. cronjob, are gated by a check_fn that fails without cron configured).
    rendered = 0
    for name in ("todo", "clarify", "cronjob"):
        defs = registry.get_definitions({name})
        if not defs:
            continue
        fn = defs[0]["function"]
        assert "input_examples" not in fn, f"{name} leaked raw input_examples"
        assert "Example calls" in fn["description"], f"{name} examples not rendered"
        rendered += 1
    assert rendered >= 2, "expected at least todo + clarify to render examples"
