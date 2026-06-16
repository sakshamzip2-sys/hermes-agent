"""Tests for the LSP code-intelligence tool (tools/lsp_tool.py).

Formatter tests are deterministic (no language server). The end-to-end query
test runs only when a language server is actually available, so the suite
passes in CI without LSP installed.
"""

import json

import pytest

from tools.lsp_tool import (
    _check_lsp,
    _format_hover,
    _format_locations,
    _format_symbols,
    _handle_lsp,
    _uri_to_path,
)


def test_uri_to_path():
    assert _uri_to_path("file:///home/u/app.py") == "/home/u/app.py"
    assert _uri_to_path("file:///a%20b/c.py") == "/a b/c.py"  # percent-decoded
    assert _uri_to_path("/already/a/path.py") == "/already/a/path.py"


def test_format_locations_1based_and_shapes():
    # Single Location (0-based LSP) → 'path:line:col' (1-based).
    loc = {"uri": "file:///x/y.py", "range": {"start": {"line": 4, "character": 2}}}
    assert _format_locations(loc)[0].endswith(":5:3")
    # LocationLink shape (targetUri/targetRange) + a list.
    link = {"targetUri": "file:///x/z.py", "targetRange": {"start": {"line": 0, "character": 0}}}
    out = _format_locations([loc, link])
    assert len(out) == 2 and out[1].endswith(":1:1")
    assert _format_locations(None) == []


def test_format_hover_variants():
    assert _format_hover({"contents": {"kind": "markdown", "value": "the type"}}) == "the type"
    assert _format_hover({"contents": "plain"}) == "plain"
    assert _format_hover({"contents": [{"value": "a"}, "b"]}) == "a\nb"
    assert _format_hover(None) == ""


def test_format_symbols_hierarchy_and_kinds():
    syms = [
        {
            "name": "Foo",
            "kind": 5,  # class
            "range": {"start": {"line": 0, "character": 0}},
            "children": [
                {"name": "bar", "kind": 6, "range": {"start": {"line": 2, "character": 4}}},
            ],
        }
    ]
    out = _format_symbols(syms)
    assert out == ["Foo (class) :1", "  bar (method) :3"]


def test_handle_lsp_validation_errors():
    # Unknown action
    assert "unknown action" in _handle_lsp({"action": "nope", "file_path": "x.py"}).lower()
    # Missing file
    assert "not found" in _handle_lsp({"action": "symbols", "file_path": "/no/such.py"}).lower()


@pytest.mark.skipif(not _check_lsp(), reason="no language server available")
def test_handle_lsp_symbols_live():
    """End-to-end: list symbols of a real repo file via the language server."""
    r = json.loads(_handle_lsp({"action": "symbols", "file_path": "tools/todo_tool.py"}))
    # Either LSP answered with symbols, or it cleanly reported unavailable.
    if r.get("ok") is False:
        pytest.skip(f"LSP not enabled for file: {r.get('reason')}")
    assert r["action"] == "symbols"
    assert r["count"] > 0
    assert any("TodoStore" in s for s in r["symbols"])
