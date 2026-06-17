"""Tests for semantic_search (STEP 8) — gated TF-IDF vector retrieval."""

import json

import pytest

from tools.semantic_search_tool import (
    semantic_search_tool,
    check_semantic_search,
    _any_index_exists,
)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    """A tiny corpus + cwd pointed at it so .agent/index lands in tmp."""
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n"
        "    # verify the user's credentials against the database\n"
        "    return check_password(user, password)\n"
    )
    (tmp_path / "retry.py").write_text(
        "def call_with_backoff(fn):\n"
        "    # retry the operation with exponential backoff on failure\n"
        "    for attempt in range(5):\n"
        "        try:\n"
        "            return fn()\n"
        "        except Exception:\n"
        "            sleep(2 ** attempt)\n"
    )
    (tmp_path / "notes.md").write_text(
        "# Architecture\n\nThe uploader validates file size and content type "
        "before storing artifacts.\n"
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _call(args):
    return json.loads(semantic_search_tool(args))


def test_index_then_search(repo):
    r = _call({"action": "index", "name": "repo", "paths": ["."]})
    assert r["indexed"] is True
    assert r["files"] == 3
    assert r["chunks"] >= 3

    res = _call({"action": "search", "name": "repo",
                 "query": "exponential backoff retry logic", "top_k": 3})
    assert res["results"], "expected results"
    # The retry.py chunk should rank first for a backoff query.
    assert res["results"][0]["path"] == "retry.py"
    assert res["results"][0]["score"] > 0
    assert "-" in res["results"][0]["lines"]  # line range present


def test_paraphrase_retrieves_chunk_grep_would_miss(repo):
    """A query phrased differently than the source still retrieves the right
    chunk — grep for the exact query string would return nothing."""
    _call({"action": "index", "name": "repo", "paths": ["."]})
    # Source says "verify the user's credentials"; query uses overlapping but
    # not identical wording. grep "authenticate a user account" → 0 hits.
    res = _call({"action": "search", "name": "repo",
                 "query": "verify user credentials password", "top_k": 1})
    assert res["results"]
    assert res["results"][0]["path"] == "auth.py"


def test_filter_restricts_paths(repo):
    _call({"action": "index", "name": "repo", "paths": ["."]})
    res = _call({"action": "search", "name": "repo", "query": "validate upload",
                 "filter": "notes.md"})
    for r in res["results"]:
        assert "notes.md" in r["path"]


def test_incremental_reindex_by_hash(repo):
    _call({"action": "index", "name": "repo", "paths": ["."]})
    # Re-index unchanged → reused_unchanged should equal the file count.
    r2 = _call({"action": "index", "name": "repo", "paths": ["."]})
    manifest = json.loads((repo / ".agent/index/repo/manifest.json").read_text())
    assert manifest["reused_unchanged"] == r2["files"]


def test_list_indexes(repo):
    _call({"action": "index", "name": "repo", "paths": ["."]})
    out = _call({"action": "list"})
    names = [i["name"] for i in out["indexes"]]
    assert "repo" in names


# --- gating: invisible until an index exists ---

def test_check_fn_hidden_without_index(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _any_index_exists() is False
    assert check_semantic_search() is False


def test_check_fn_visible_with_index(repo):
    _call({"action": "index", "name": "repo", "paths": ["."]})
    assert check_semantic_search() is True


def test_registered_lazy_and_gated():
    import tools.semantic_search_tool  # noqa: F401
    from tools.registry import registry
    entry = registry._tools.get("semantic_search")
    assert entry is not None
    assert entry.check_fn is not None  # gated
    from toolsets import _HERMES_CORE_TOOLS
    assert "semantic_search" not in _HERMES_CORE_TOOLS  # not core


def test_search_missing_index_errors(repo):
    res = _call({"action": "search", "name": "nonexistent", "query": "x"})
    assert "error" in res


def test_unknown_action_errors(repo):
    res = _call({"action": "frobnicate"})
    assert "error" in res
