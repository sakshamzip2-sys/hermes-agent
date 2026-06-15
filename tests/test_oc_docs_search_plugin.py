"""Tests for the oc_docs_search plugin (ported v1 OcDocs).

Stdlib + pytest only, no network. Builds a tiny temp docs tree and exercises
chunking, BM25 ranking, the JSON cache round-trip, and the tool handler's
output contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.oc_docs_search.docs_index import (
    OcDocsIndex,
    _BM25,
    _chunk_markdown,
    _split_section_respecting_fences,
)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_bm25_ranks_relevant_doc_first():
    corpus = [
        ["prompt", "caching", "is", "sacred"],
        ["the", "footprint", "ladder", "decides", "new", "tools"],
        ["unrelated", "spotify", "playback"],
    ]
    bm = _BM25(corpus)
    scores = bm.get_scores(["footprint", "ladder"])
    assert scores[1] == max(scores)
    assert scores[1] > 0
    # a term in no doc contributes nothing, never errors
    assert bm.get_scores(["nonexistentterm"]) == [0.0, 0.0, 0.0]


def test_chunker_preserves_code_fences():
    md = (
        "# Title\n\n"
        "Intro paragraph.\n\n"
        "```python\n" + "x = 1\n" * 80 + "```\n\n"
        "Trailing paragraph.\n"
    )
    pieces = _split_section_respecting_fences(md.split("# Title\n\n", 1)[1])
    # The fenced block must never be split: exactly one piece contains the fence
    # open, and that same piece contains the close (balanced fence count).
    fenced = [p for p in pieces if "```" in p]
    assert fenced, "fence should survive chunking"
    for p in fenced:
        assert p.count("```") % 2 == 0, "a chunk must not contain a half-open fence"


def test_chunk_markdown_carries_header_trail():
    md = "# Top\n\n## Sub\n\nbody text here\n"
    chunks = _chunk_markdown(md, source=Path("x.md"))
    assert chunks
    assert chunks[0].headers == ("Top", "Sub")
    assert "Top > Sub" in chunks[0].text


def test_index_query_and_cache_roundtrip(tmp_path: Path):
    docs = tmp_path / "docs"
    _write(docs / "a.md", "# Caching\n\nPer-conversation prompt caching is sacred.\n")
    _write(docs / "b.md", "# Tools\n\nThe footprint ladder governs new core tools.\n")
    _write(docs / "refs" / "skip.md", "# Refs\n\nthis is in refs and must be excluded\n")
    cache = tmp_path / "cache"

    idx = OcDocsIndex(docs_roots=[docs], cache_root=cache)
    assert idx.total_indexed >= 2
    hits = idx.query("footprint ladder", top_k=3)
    assert hits, "expected at least one hit"
    assert hits[0].chunk.path.name == "b.md"
    # refs/ excluded
    assert all(h.chunk.path.name != "skip.md" for h in hits)

    # cache file written, and a second index reuses it (same results)
    assert (cache / "oc_docs_bm25.json").exists()
    idx2 = OcDocsIndex(docs_roots=[docs], cache_root=cache)
    assert idx2.total_indexed == idx.total_indexed
    assert idx2.query("footprint ladder", top_k=1)[0].chunk.path.name == "b.md"


def test_handler_output_contract(tmp_path: Path, monkeypatch):
    docs = tmp_path / "docs"
    _write(docs / "a.md", "# Delegation\n\nDelegate spawns an isolated subagent with its own toolsets.\n")
    monkeypatch.setenv("OC_DOCS_SEARCH_ROOTS", str(docs))

    import plugins.oc_docs_search as plugin

    monkeypatch.setattr(plugin, "_INDEX", None)  # force rebuild against temp roots
    monkeypatch.setattr(plugin, "_cache_root", lambda: tmp_path / "cache")

    out = plugin._docs_search("subagent toolsets", top_k=2)
    data = json.loads(out)
    assert data["query"] == "subagent toolsets"
    assert data["total_indexed"] >= 1
    assert data["matches"], "expected matches"
    m = data["matches"][0]
    assert set(m) == {"path", "headers", "snippet", "score"}
    assert "a.md" in m["path"]

    # empty query is handled gracefully
    assert json.loads(plugin._docs_search("  ", top_k=2))["matches"] == []


def test_check_requirements_gates_on_docs(tmp_path: Path, monkeypatch):
    import plugins.oc_docs_search as plugin

    monkeypatch.setenv("OC_DOCS_SEARCH_ROOTS", str(tmp_path / "does_not_exist"))
    assert plugin._check_requirements() is False

    real = tmp_path / "docs"
    _write(real / "a.md", "# X\n\ncontent\n")
    monkeypatch.setenv("OC_DOCS_SEARCH_ROOTS", str(real))
    assert plugin._check_requirements() is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
