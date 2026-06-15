"""oc_docs_search plugin — BM25 search over the repo's markdown docs.

Ported from OpenComputer v1's ``OcDocs`` tool into the v2 plugin idiom
(Footprint Ladder rung 4: a plugin, not a core tool — zero model-schema
footprint until enabled via ``plugins.enabled``). The agent calls
``docs_search`` EXPLICITLY when it needs to locate a concept documented in
the project tree; there is no per-turn injection.

The heavy lifting (header-aware chunking, fence preservation, incremental
JSON cache) lives in ``docs_index.py``, carried over from v1 with a
pure-stdlib BM25 swapped in for the third-party ``rank_bm25`` dependency.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo root = two levels up from this file (plugins/oc_docs_search/__init__.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_MAX_LIMIT = 20
_DEFAULT_LIMIT = 5
_SNIPPET_CAP = 280

DOCS_SEARCH_SCHEMA = {
    "name": "docs_search",
    "description": (
        "Search the OpenComputer project documentation (markdown) by keyword "
        "and return the most relevant passages with their header trail and "
        "source path. Call this to find where a concept, config key, command, "
        "or subsystem is documented before reading a whole file. Returns the "
        "top matches ranked by BM25; follow up with `read_file` on a returned "
        "path for full context."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Keywords or a natural-language phrase to search for.",
            },
            "top_k": {
                "type": "integer",
                "description": f"Number of passages to return (1-{_MAX_LIMIT}, default {_DEFAULT_LIMIT}).",
            },
        },
        "required": ["query"],
    },
}


def _resolve_doc_roots() -> list[Path]:
    """Doc roots to index.

    Override with the ``OC_DOCS_SEARCH_ROOTS`` env var (os.pathsep-separated
    absolute paths). Default: the v2 repo's ``docs/``, ``website/docs/``, and
    its top-level ``*.md`` files.
    """
    override = os.environ.get("OC_DOCS_SEARCH_ROOTS", "").strip()
    if override:
        return [Path(p) for p in override.split(os.pathsep) if p.strip()]
    roots: list[Path] = []
    for sub in ("docs", "website/docs"):
        d = _REPO_ROOT / sub
        if d.exists():
            roots.append(d)
    # top-level *.md (AGENTS.md, README.md, CONTRIBUTING.md, ...)
    roots.extend(sorted(_REPO_ROOT.glob("*.md")))
    return roots


def _cache_root() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return get_hermes_home() / "cache" / "oc_docs_search"
    except Exception:
        return _REPO_ROOT / ".oc_docs_cache"


def _check_requirements() -> bool:
    """Tool only appears when there is at least one doc root with content."""
    return any(p.exists() for p in _resolve_doc_roots())


# Built lazily on first call so plugin import stays cheap and the BM25 corpus
# is constructed only when the agent actually uses the tool.
_INDEX = None


def _get_index():
    global _INDEX
    if _INDEX is None:
        from plugins.oc_docs_search.docs_index import OcDocsIndex

        _INDEX = OcDocsIndex(docs_roots=_resolve_doc_roots(), cache_root=_cache_root())
    return _INDEX


def _docs_search(query: str, top_k: int = _DEFAULT_LIMIT) -> str:
    query = (query or "").strip()
    if not query:
        return json.dumps({"error": "query is required", "matches": []})
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = _DEFAULT_LIMIT
    top_k = max(1, min(top_k, _MAX_LIMIT))

    try:
        index = _get_index()
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("oc_docs_search index build failed: %s", e)
        return json.dumps({"error": f"index build failed: {e}", "matches": []})

    hits = index.query(query, top_k=top_k)
    matches = []
    for h in hits:
        snippet = h.chunk.text.strip()
        if len(snippet) > _SNIPPET_CAP:
            snippet = snippet[: _SNIPPET_CAP - 1].rstrip() + "…"
        try:
            path_str = str(h.chunk.path.relative_to(_REPO_ROOT))
        except ValueError:
            path_str = str(h.chunk.path)
        matches.append(
            {
                "path": path_str,
                "headers": list(h.chunk.headers),
                "snippet": snippet,
                "score": round(h.score, 3),
            }
        )
    return json.dumps(
        {"query": query, "total_indexed": index.total_indexed, "matches": matches}
    )


def register(ctx) -> None:
    """Register the ``docs_search`` tool. Enabled via ``plugins.enabled``."""
    ctx.register_tool(
        name="docs_search",
        toolset="search",
        schema=DOCS_SEARCH_SCHEMA,
        handler=lambda args, **kw: _docs_search(
            query=args.get("query", ""),
            top_k=args.get("top_k", _DEFAULT_LIMIT),
        ),
        check_fn=_check_requirements,
        description="BM25 search over the project's markdown docs.",
        emoji="\U0001f4d6",  # 📖
    )
    logger.debug("oc_docs_search: registered docs_search tool")
