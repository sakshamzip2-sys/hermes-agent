"""Tests for web_extract_structured (STEP 6) — mocked fetch + LLM, no live calls."""

import json

import pytest

from tools.web_extract_structured_tool import (
    web_extract_structured_tool,
    _parse_json_payload,
    check_web_extract_structured,
)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.fixture()
def patched_backends(monkeypatch):
    """Mock the web_extract fetch + the auxiliary LLM call."""
    async def fake_web_extract(urls, format="markdown", use_llm_processing=True):
        return json.dumps({"results": [
            {"url": u, "content": f"Page for {u}: Widget X costs $42 and is in stock."}
            for u in urls
        ]})

    async def fake_async_call_llm(**kwargs):
        # Return a JSON value matching the schema in the user prompt.
        return {"choices": [{"message": {"content":
                json.dumps({"name": "Widget X", "price": 42, "in_stock": True})}}]}

    def fake_resolve_aux(model=None):
        return (object(), "mock/aux-model", {})

    def fake_extract_content(response):
        return response["choices"][0]["message"]["content"]

    import tools.web_tools as wt
    monkeypatch.setattr(wt, "web_extract_tool", fake_web_extract)
    monkeypatch.setattr(wt, "async_call_llm", fake_async_call_llm)
    monkeypatch.setattr(wt, "_resolve_web_extract_auxiliary", fake_resolve_aux)
    monkeypatch.setattr(wt, "extract_content_or_reasoning", fake_extract_content)


def test_structured_extraction_against_schema(patched_backends):
    schema = {"type": "object", "properties": {
        "name": {"type": "string"}, "price": {"type": "number"},
        "in_stock": {"type": "boolean"}}}
    out = json.loads(_run(web_extract_structured_tool(
        {"url": "https://example.com/p/1", "schema": schema})))
    assert out["format"] == "structured"
    assert len(out["results"]) == 1
    r = out["results"][0]
    assert r["url"] == "https://example.com/p/1"
    assert r["data"] == {"name": "Widget X", "price": 42, "in_stock": True}
    assert r["schema_valid"] is True


def test_markdown_when_no_schema(patched_backends):
    out = json.loads(_run(web_extract_structured_tool({"url": "https://example.com/a"})))
    assert out["format"] == "markdown"
    assert "Widget X" in out["results"][0]["markdown"]


def test_multiple_urls(patched_backends):
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    out = json.loads(_run(web_extract_structured_tool(
        {"urls": ["https://a.com", "https://b.com"], "schema": schema})))
    assert len(out["results"]) == 2


def test_missing_url_errors():
    out = json.loads(_run(web_extract_structured_tool({})))
    assert "error" in out


# --- JSON payload parsing (the LLM-output robustness layer) ---

def test_parse_plain_json():
    data, valid = _parse_json_payload('{"a": 1}')
    assert data == {"a": 1} and valid


def test_parse_fenced_json():
    data, valid = _parse_json_payload('```json\n{"a": 1}\n```')
    assert data == {"a": 1} and valid


def test_parse_json_with_surrounding_prose():
    data, valid = _parse_json_payload('Here is the result: {"a": 1} hope it helps')
    assert data == {"a": 1} and valid


def test_parse_invalid_json():
    data, valid = _parse_json_payload("not json at all")
    assert data is None and not valid


# --- registration: LAZY, not core ---

def test_registered_as_lazy_not_core():
    import tools.web_extract_structured_tool  # noqa: F401 — trigger registration
    from tools.registry import registry
    entry = registry._tools.get("web_extract_structured")
    assert entry is not None
    assert entry.toolset == "web_research"  # non-core → deferred
    from toolsets import _HERMES_CORE_TOOLS
    assert "web_extract_structured" not in _HERMES_CORE_TOOLS
    from tools.tool_search import is_deferrable_tool_name
    assert is_deferrable_tool_name("web_extract_structured") is True


def test_check_fn_callable():
    # Should not raise; returns a bool.
    assert isinstance(check_web_extract_structured(), bool)
