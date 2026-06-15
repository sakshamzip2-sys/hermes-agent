"""BM25 retrieval index over a tree of ``*.md`` docs.

Ported from OpenComputer v1 (``opencomputer/agent/oc_docs_index.py``) into the
v2 plugin idiom. The header-aware, code-fence-preserving chunker and the
incremental per-file content-hash JSON cache are carried over verbatim; the
only change is the scorer: v1 depended on the third-party ``rank_bm25``
package, this port ships a small pure-stdlib BM25 so the plugin is drop-in
with zero new dependencies.

Design constraints (from the v1 module):
  - BM25 first (deterministic, no embeddings)
  - Header-aware chunking (a leaf chunk carries its full header trail)
  - Code-fence preservation (never split inside a ``` ... ``` block)
  - <=500-char chunks
  - per-file content-hash for incremental rebuild
  - JSON cache only (never pickle — CWE-502)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CHUNK_CAP_CHARS = 500
_FENCE_TOKEN = "```"
CACHE_FORMAT_VERSION = 2


@dataclass(frozen=True)
class Chunk:
    """One indexable chunk — atomic for BM25 scoring + display."""

    path: Path
    headers: tuple[str, ...]  # ordered: root -> leaf
    text: str


@dataclass(frozen=True)
class DocHit:
    chunk: Chunk
    score: float
    rank: int  # 0-indexed


# ── pure-stdlib BM25 (replaces rank_bm25.BM25Okapi) ────────────────────────


class _BM25:
    """Okapi BM25 over pre-tokenized documents, pure stdlib.

    Uses the non-negative ``log(1 + (N - n + 0.5)/(n + 0.5))`` idf variant
    (BM25+ style) so a term present in most docs can never contribute a
    negative score — robust without rank_bm25's average-idf epsilon dance.
    An inverted index keeps scoring O(query_terms x matching_docs).
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.n_docs = len(corpus_tokens)
        self.doc_len = [len(d) for d in corpus_tokens]
        self.avgdl = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0
        # postings: term -> list[(doc_idx, term_freq)]
        self.postings: dict[str, list[tuple[int, int]]] = {}
        for idx, doc in enumerate(corpus_tokens):
            freqs: dict[str, int] = {}
            for tok in doc:
                freqs[tok] = freqs.get(tok, 0) + 1
            for tok, f in freqs.items():
                self.postings.setdefault(tok, []).append((idx, f))
        self.idf: dict[str, float] = {}
        for tok, plist in self.postings.items():
            n = len(plist)
            self.idf[tok] = math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5))

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.n_docs
        if not self.avgdl:
            return scores
        for tok in query_tokens:
            idf = self.idf.get(tok)
            if idf is None:
                continue
            for idx, f in self.postings.get(tok, ()):  # only docs that contain tok
                denom = f + self.k1 * (1 - self.b + self.b * self.doc_len[idx] / self.avgdl)
                scores[idx] += idf * (f * (self.k1 + 1)) / denom
        return scores


# ── chunking (ported verbatim from v1) ─────────────────────────────────────


def _content_hash(text: str) -> str:
    # SHA-256 used purely for cache change-detection, not as a signature.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_into_sections(md: str) -> list[tuple[tuple[str, ...], str]]:
    """Split markdown into ``(header_trail, body)`` pairs.

    Headers stack: a ``###`` keeps the surrounding ``##`` + ``#`` in the
    trail so the agent sees the full context of a leaf chunk.
    """
    lines = md.splitlines()
    stack: list[tuple[int, str]] = []  # (level, text)
    cur_body: list[str] = []
    sections: list[tuple[tuple[str, ...], str]] = []

    def flush() -> None:
        if not cur_body:
            return
        body = "\n".join(cur_body).strip()
        if not body:
            cur_body.clear()
            return
        trail = tuple(text for _, text in stack)
        sections.append((trail, body))
        cur_body.clear()

    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            text = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, text))
            continue
        cur_body.append(line)
    flush()
    if not sections and md.strip():
        sections.append(((), md.strip()))
    return sections


def _split_section_respecting_fences(body: str) -> list[str]:
    """Split a section body into <=_CHUNK_CAP_CHARS pieces, never breaking
    inside a fenced code block."""
    paragraphs = re.split(r"\n\s*\n", body)
    chunks: list[str] = []
    cur: list[str] = []
    cur_size = 0
    in_fence = False

    def cur_size_plus(p: str) -> int:
        return cur_size + (2 if cur else 0) + len(p)

    for p in paragraphs:
        if p.count(_FENCE_TOKEN) % 2 == 1:
            in_fence = not in_fence
        would_exceed = cur_size_plus(p) > _CHUNK_CAP_CHARS
        if would_exceed and cur and not in_fence:
            chunks.append("\n\n".join(cur))
            cur = [p]
            cur_size = len(p)
        else:
            cur.append(p)
            cur_size = cur_size_plus(p)
    if cur:
        chunks.append("\n\n".join(cur))

    out: list[str] = []
    for c in chunks:
        if not c.strip():
            continue
        if len(c) <= _CHUNK_CAP_CHARS or c.count(_FENCE_TOKEN) % 2 == 1:
            out.append(c)
            continue
        out.extend(_word_wrap(c, _CHUNK_CAP_CHARS))
    return out


def _word_wrap(text: str, cap: int) -> list[str]:
    """Split ``text`` into <=``cap``-char pieces on whitespace boundaries."""
    tokens = text.split()
    out: list[str] = []
    cur: list[str] = []
    cur_size = 0
    for t in tokens:
        add = len(t) + (1 if cur else 0)
        if cur and cur_size + add > cap:
            out.append(" ".join(cur))
            cur = [t]
            cur_size = len(t)
        else:
            cur.append(t)
            cur_size += add
    if cur:
        out.append(" ".join(cur))
    return out


def _chunk_markdown(md: str, *, source: Path) -> list[Chunk]:
    """Header-aware chunking of a markdown doc."""
    out: list[Chunk] = []
    for headers, body in _split_into_sections(md):
        for piece in _split_section_respecting_fences(body):
            indexed = " > ".join(headers) + "\n\n" + piece if headers else piece
            out.append(Chunk(path=source, headers=headers, text=indexed))
    return out


class OcDocsIndex:
    """BM25 index over one or more doc roots (``*.md``).

    Construct once per query session; the constructor walks the trees, chunks
    each file, and builds the BM25 corpus. A JSON cache is consulted first; on
    a hit only changed files are re-chunked.
    """

    def __init__(self, *, docs_roots: list[Path], cache_root: Path, exclude_segments: tuple[str, ...] = ("refs", "node_modules")) -> None:
        self.docs_roots = [Path(r) for r in docs_roots]
        self.cache_root = Path(cache_root)
        self.exclude_segments = exclude_segments
        self._chunks: list[Chunk] = []
        self._bm25: _BM25 | None = None
        self._build()

    # ── public API ────────────────────────────────────────────────

    @property
    def total_indexed(self) -> int:
        return len(self._chunks)

    def query(self, text: str, top_k: int = 5) -> list[DocHit]:
        if self._bm25 is None or not self._chunks:
            return []
        tokens = self._tokenize(text)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        scored = sorted(enumerate(scores), key=lambda kv: (-kv[1], kv[0]))
        hits: list[DocHit] = []
        for rank, (idx, score) in enumerate(scored[:top_k]):
            if score <= 0:
                continue
            hits.append(DocHit(chunk=self._chunks[idx], score=float(score), rank=rank))
        return hits

    # ── internals ─────────────────────────────────────────────────

    def _cache_file(self) -> Path:
        return self.cache_root / "oc_docs_bm25.json"

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())

    def _excluded(self, rel: Path) -> bool:
        return any(seg in self.exclude_segments for seg in rel.parts)

    def _iter_md_files(self) -> list[Path]:
        files: list[Path] = []
        for root in self.docs_roots:
            if not root.exists():
                continue
            if root.is_file() and root.suffix == ".md":
                files.append(root)
                continue
            for f in root.rglob("*.md"):
                if self._excluded(f.relative_to(root)):
                    continue
                files.append(f)
        # de-dupe + stable order
        return sorted(set(files), key=str)

    def _build(self) -> None:
        files = self._iter_md_files()
        if not files:
            self._chunks = []
            self._bm25 = None
            return

        file_hashes: dict[str, str] = {}
        for f in files:
            try:
                file_hashes[str(f)] = _content_hash(f.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                logger.warning("oc_docs: cannot read %s: %s", f, e)

        cached = self._load_cache()
        if cached is not None and cached.get("file_hashes") == file_hashes:
            self._chunks = cached["chunks"]
            self._bm25 = _BM25([self._tokenize(c.text) for c in self._chunks]) if self._chunks else None
            return

        prev_chunks_by_path: dict[str, list[Chunk]] = {}
        prev_hashes: dict[str, str] = {}
        if cached is not None:
            for c in cached.get("chunks", []):
                prev_chunks_by_path.setdefault(str(c.path), []).append(c)
            prev_hashes = cached.get("file_hashes", {})

        new_chunks: list[Chunk] = []
        for f in files:
            f_key = str(f)
            h = file_hashes.get(f_key)
            if h is not None and h == prev_hashes.get(f_key):
                new_chunks.extend(prev_chunks_by_path.get(f_key, []))
                continue
            try:
                md = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            new_chunks.extend(_chunk_markdown(md, source=f))

        self._chunks = new_chunks
        self._bm25 = _BM25([self._tokenize(c.text) for c in self._chunks]) if self._chunks else None
        self._save_cache(file_hashes)

    def _load_cache(self) -> dict | None:
        cache_file = self._cache_file()
        if not cache_file.exists():
            return None
        try:
            with cache_file.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                return None
            if payload.get("format_version") != CACHE_FORMAT_VERSION:
                return None
            raw_chunks = payload.get("chunks")
            if not isinstance(raw_chunks, list):
                return None
            payload["chunks"] = [
                Chunk(
                    path=Path(str(d["path"])),
                    headers=tuple(str(h) for h in d["headers"]),
                    text=str(d["text"]),
                )
                for d in raw_chunks
            ]
            return payload
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, KeyError, AttributeError, TypeError, ValueError) as e:
            logger.warning("oc_docs cache load failed: %s", e)
            return None

    def _save_cache(self, file_hashes: dict[str, str]) -> None:
        try:
            self.cache_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("oc_docs cache dir create failed: %s", e)
            return
        payload = {
            "format_version": CACHE_FORMAT_VERSION,
            "file_hashes": file_hashes,
            "chunks": [
                {"path": str(c.path), "headers": list(c.headers), "text": c.text}
                for c in self._chunks
            ],
        }
        try:
            with self._cache_file().open("w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError as e:
            logger.warning("oc_docs cache write failed: %s", e)
