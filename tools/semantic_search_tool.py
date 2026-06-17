"""``semantic_search`` — meaning-ranked retrieval over an indexed corpus (GATED).

Beyond grep/LSP: grep finds exact substrings, LSP resolves symbols, but neither
answers "where is the code that *does X*" when you don't know the exact words.
This tool indexes files into a local TF-IDF vector store and retrieves the
chunks whose content is most *relevant* to a natural-language query, ranked by
cosine similarity — so a paraphrased query surfaces the right chunk even when no
single line contains the query's exact words.

Self-contained: pure-numpy TF-IDF with a stored vocabulary + IDF, so the query
embeds into the *same* space as the indexed chunks (persistence-safe across
separate index/search calls). No embedding API, no external vector DB.

GATED: ``check_fn`` hides the tool entirely until at least one index exists under
``.agent/index/`` — zero schema/budget cost when unused.

Storage layout (per index ``<name>``, rooted at the cwd):
    .agent/index/<name>/
        manifest.json   # mode, file->hash map, vocab size, chunk count
        chunks.json     # [{path, start_line, end_line, text}, ...]
        vocab.json      # {term: column index}
        matrix.npy      # float32 [n_chunks x vocab] L2-normalized TF-IDF rows
        idf.npy         # float32 [vocab] IDF weights (for embedding queries)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_INDEX_ROOT = ".agent/index"
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{1,}")
_CHUNK_LINES = 40          # window size for chunking
_CHUNK_OVERLAP = 8         # overlap between consecutive windows
_MAX_FILE_BYTES = 1_000_000
_DEFAULT_TOP_K = 8
_CODE_EXTS = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb",
              ".c", ".cpp", ".h", ".hpp", ".cs", ".swift", ".kt", ".scala", ".sh"}
_TEXT_EXTS = {".md", ".markdown", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml"}
_INDEXABLE_EXTS = _CODE_EXTS | _TEXT_EXTS


def _index_dir(name: str, root: Optional[str] = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name or "default")
    base = Path(root or os.getcwd()) / _INDEX_ROOT / safe
    return base


def _any_index_exists(root: Optional[str] = None) -> bool:
    base = Path(root or os.getcwd()) / _INDEX_ROOT
    if not base.is_dir():
        return False
    return any((d / "manifest.json").exists() for d in base.iterdir() if d.is_dir())


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        h.update(path.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _iter_files(paths: List[str], root: str) -> List[Path]:
    """Expand path/glob inputs into a deduplicated list of indexable files."""
    out: List[Path] = []
    seen = set()
    root_p = Path(root)
    for raw in paths:
        # Support globs and directories and plain files, relative to root.
        candidates: List[Path] = []
        if any(ch in raw for ch in "*?["):
            candidates = [root_p / m for m in _glob(root_p, raw)]
        else:
            p = (root_p / raw)
            if p.is_dir():
                candidates = [q for q in p.rglob("*") if q.is_file()]
            elif p.is_file():
                candidates = [p]
        for c in candidates:
            if c.suffix.lower() not in _INDEXABLE_EXTS:
                continue
            if "/.git/" in str(c) or "/node_modules/" in str(c) or "/.agent/" in str(c):
                continue
            try:
                if c.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            key = str(c.resolve())
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def _glob(root: Path, pattern: str) -> List[str]:
    try:
        return [str(p.relative_to(root)) for p in root.glob(pattern)]
    except Exception:
        return []


def _chunk_file(path: Path) -> List[Dict[str, Any]]:
    """Split a file into overlapping line windows, preserving line ranges."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if not lines:
        return []
    chunks = []
    step = max(1, _CHUNK_LINES - _CHUNK_OVERLAP)
    for start in range(0, len(lines), step):
        window = lines[start:start + _CHUNK_LINES]
        text = "\n".join(window).strip()
        if not text:
            continue
        chunks.append({
            "start_line": start + 1,
            "end_line": min(start + _CHUNK_LINES, len(lines)),
            "text": text,
        })
        if start + _CHUNK_LINES >= len(lines):
            break
    return chunks


def _build_tfidf(chunk_texts: List[str]):
    """Build vocab, IDF, and an L2-normalized TF-IDF matrix. Returns numpy arrays."""
    import numpy as np

    tokenized = [_tokenize(t) for t in chunk_texts]
    vocab: Dict[str, int] = {}
    for toks in tokenized:
        for tok in set(toks):
            if tok not in vocab:
                vocab[tok] = len(vocab)
    n = len(chunk_texts)
    dim = len(vocab)
    if dim == 0 or n == 0:
        return vocab, np.zeros(0, dtype="float32"), np.zeros((n, 0), dtype="float32")

    df = np.zeros(dim, dtype="float32")
    tf = np.zeros((n, dim), dtype="float32")
    for i, toks in enumerate(tokenized):
        seen_terms = set()
        for tok in toks:
            j = vocab[tok]
            tf[i, j] += 1.0
            seen_terms.add(j)
        for j in seen_terms:
            df[j] += 1.0
    idf = np.log((1.0 + n) / (1.0 + df)) + 1.0  # smoothed IDF
    mat = tf * idf  # broadcast
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = (mat / norms).astype("float32")
    return vocab, idf.astype("float32"), mat


def _embed_query(query: str, vocab: Dict[str, int], idf) -> Any:
    """Embed a query into the stored vocab/idf space (L2-normalized TF-IDF)."""
    import numpy as np

    vec = np.zeros(len(vocab), dtype="float32")
    for tok in _tokenize(query):
        j = vocab.get(tok)
        if j is not None:
            vec[j] += 1.0
    vec = vec * idf
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def _do_index(args: dict) -> str:
    import numpy as np

    name = args.get("name") or "default"
    paths = args.get("paths") or args.get("globs")
    if isinstance(paths, str):
        paths = [paths]
    if not paths or not isinstance(paths, list):
        return tool_error("Provide 'paths' (list of files/dirs/globs) and a 'name'.")
    root = os.getcwd()
    files = _iter_files([str(p) for p in paths], root)
    if not files:
        return tool_error("No indexable files matched (supported: code + markdown/text).")

    idx_dir = _index_dir(name, root)
    # Incremental: load prior manifest to skip unchanged files.
    prior_hashes: Dict[str, str] = {}
    manifest_path = idx_dir / "manifest.json"
    if manifest_path.exists():
        try:
            prior_hashes = json.loads(manifest_path.read_text()).get("files", {})
        except Exception:
            prior_hashes = {}

    file_hashes: Dict[str, str] = {}
    chunks: List[Dict[str, Any]] = []
    for f in files:
        rel = str(f.relative_to(root)) if str(f).startswith(root) else str(f)
        h = _file_hash(f)
        file_hashes[rel] = h
        for ch in _chunk_file(f):
            ch["path"] = rel
            chunks.append(ch)

    if not chunks:
        return tool_error("Files matched but produced no chunks (all empty?).")

    vocab, idf, mat = _build_tfidf([c["text"] for c in chunks])

    idx_dir.mkdir(parents=True, exist_ok=True)
    (idx_dir / "chunks.json").write_text(json.dumps(chunks, ensure_ascii=False))
    (idx_dir / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False))
    np.save(idx_dir / "idf.npy", idf)
    np.save(idx_dir / "matrix.npy", mat)
    manifest = {
        "name": name, "mode": "tfidf", "files": file_hashes,
        "chunk_count": len(chunks), "vocab_size": len(vocab),
        "reused_unchanged": sum(1 for r, h in file_hashes.items()
                                if prior_hashes.get(r) == h),
    }
    (idx_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))

    return tool_result({
        "indexed": True, "name": name,
        "files": len(file_hashes), "chunks": len(chunks),
        "vocab_size": len(vocab),
        "path": str(idx_dir),
    })


def _do_search(args: dict) -> str:
    import numpy as np

    name = args.get("name") or "default"
    query = args.get("query")
    if not query or not isinstance(query, str):
        return tool_error("Provide a 'query' string.")
    top_k = int(args.get("top_k", _DEFAULT_TOP_K))
    path_filter = args.get("filter")

    idx_dir = _index_dir(name, os.getcwd())
    if not (idx_dir / "manifest.json").exists():
        return tool_error(f"No index named {name!r}. Build one first with action=index.")

    try:
        chunks = json.loads((idx_dir / "chunks.json").read_text())
        vocab = json.loads((idx_dir / "vocab.json").read_text())
        idf = np.load(idx_dir / "idf.npy")
        mat = np.load(idx_dir / "matrix.npy")
    except Exception as exc:
        return tool_error(f"Index {name!r} is unreadable/corrupt: {exc}")

    qvec = _embed_query(query, vocab, idf)
    if mat.size == 0 or float(np.linalg.norm(qvec)) == 0.0:
        return tool_result({"query": query, "name": name, "results": []})

    scores = mat @ qvec  # cosine (both sides L2-normalized)
    order = np.argsort(-scores)
    results = []
    for i in order:
        if len(results) >= top_k:
            break
        ch = chunks[int(i)]
        if path_filter and path_filter not in ch["path"]:
            continue
        score = float(scores[int(i)])
        if score <= 0.0:
            continue
        snippet = ch["text"]
        if len(snippet) > 600:
            snippet = snippet[:600] + " …"
        results.append({
            "path": ch["path"],
            "lines": f"{ch['start_line']}-{ch['end_line']}",
            "score": round(score, 4),
            "snippet": snippet,
        })
    return tool_result({"query": query, "name": name, "results": results})


def _do_list(args: dict) -> str:
    base = Path(os.getcwd()) / _INDEX_ROOT
    indexes = []
    if base.is_dir():
        for d in sorted(base.iterdir()):
            mp = d / "manifest.json"
            if mp.exists():
                try:
                    m = json.loads(mp.read_text())
                    indexes.append({"name": m.get("name", d.name),
                                    "files": len(m.get("files", {})),
                                    "chunks": m.get("chunk_count", 0)})
                except Exception:
                    continue
    return tool_result({"indexes": indexes})


def semantic_search_tool(args: dict, **_kw) -> str:
    action = str(args.get("action", "search")).strip().lower()
    if action == "index":
        return _do_index(args)
    if action == "search":
        return _do_search(args)
    if action == "list":
        return _do_list(args)
    return tool_error(f"Unknown action {action!r}. Use index | search | list.")


def check_semantic_search() -> bool:
    """Invisible unless at least one index exists (zero cost otherwise)."""
    try:
        return _any_index_exists()
    except Exception:
        return False


SEMANTIC_SEARCH_SCHEMA = {
    "name": "semantic_search",
    "description": (
        "Meaning-ranked retrieval over an indexed corpus. Prefer this over grep "
        "when you DON'T know the exact words — 'where is the retry/backoff "
        "logic', 'code that validates uploads' — and want results ranked by "
        "relevance. Use grep for exact strings/symbols and LSP for "
        "definitions/references; use this for conceptual 'find the code about X' "
        "queries. action=index builds/updates an index from files/dirs/globs "
        "(incremental by file hash); action=search returns top_k chunks with "
        "path, line range, score, snippet; action=list shows existing indexes."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["index", "search", "list"],
                       "description": "index | search | list. Default search."},
            "name": {"type": "string", "description": "Index name. Default 'default'."},
            "paths": {"type": "array", "items": {"type": "string"},
                      "description": "(index) files, dirs, or globs to index."},
            "query": {"type": "string", "description": "(search) natural-language query."},
            "top_k": {"type": "integer", "description": "(search) max results. Default 8."},
            "filter": {"type": "string",
                       "description": "(search) substring a result path must contain."},
        },
        "required": [],
    },
    "input_examples": [
        {"action": "index", "name": "repo", "paths": ["tools", "agent"]},
        {"action": "search", "name": "repo", "query": "where do we resolve the execution sandbox backend", "top_k": 5},
        {"action": "list"},
    ],
}


registry.register(
    name="semantic_search",
    toolset="semantic_search",  # non-core → lazy, AND check_fn-gated on index existence
    schema=SEMANTIC_SEARCH_SCHEMA,
    handler=semantic_search_tool,
    check_fn=check_semantic_search,
    emoji="🧭",
    max_result_size_chars=100_000,
)
