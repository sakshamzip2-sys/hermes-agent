"""Immutable memory versioning — Anthropic memory-stores semantics over the v2 files.

Every committed write to a memory file (MEMORY.md / USER.md / DREAMS.md / a dream output
store) is snapshotted as an immutable VERSION addressed by ``memver_...``, giving:

* a full **audit trail** (who/when/why for every change),
* **point-in-time recovery** (:meth:`restore` any past version), and
* **redaction** (:meth:`redact` scrubs sensitive bytes from a historical snapshot while
  keeping the audit row — PII/secret/deletion compliance).

This is the safety net that makes the self-evolution loop's AUTONOMOUS writes reversible
(the owner-locked posture: "autonomous + versioned rollback + redact"). It's a thin,
dependency-free log: content-addressed snapshots + an append-only ``index.jsonl``.

Layout under ``<root>/`` (default ``$HERMES_HOME/memories/.versions``)::

    <root>/index.jsonl                 # one JSON row per version (append-only)
    <root>/snaps/<sha256>.snap         # content-addressed immutable snapshots

Snapshots are content-addressed, so identical content is stored once; the index row is
what carries per-version identity (``memver``), ordering, and the redaction flag.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger("hermes.agent.memory_versioning")

# Anthropic guidance: keep individual memory files small (~100kB / ~25k tokens).
SOFT_CAP_BYTES = 100 * 1024
_REDACTION = b"[REDACTED]"


def _new_memver() -> str:
    return "memver_" + uuid.uuid4().hex


class MemoryVersionLog:
    """Append-only, content-addressed version log for memory files."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.snaps = self.root / "snaps"
        self.index_path = self.root / "index.jsonl"
        self.snaps.mkdir(parents=True, exist_ok=True)

    # -- write -----------------------------------------------------------------
    def record_version(
        self,
        file_name: str,
        content: bytes,
        *,
        op: str = "write",
        actor: str = "",
        ts: float,
    ) -> str:
        """Snapshot ``content`` as a new immutable version of ``file_name``; return memver."""
        if isinstance(content, str):
            content = content.encode("utf-8")
        sha = hashlib.sha256(content).hexdigest()
        self._write_snapshot(sha, content)
        prev = self._latest_memver(file_name)
        memver = _new_memver()
        row = {
            "memver": memver,
            "file": str(file_name),
            "ts": float(ts),
            "op": str(op),
            "actor": str(actor),
            "sha256": sha,
            "prev_memver": prev,
            "bytes": len(content),
            "over_soft_cap": len(content) > SOFT_CAP_BYTES,
            "redacted": False,
        }
        if row["over_soft_cap"]:
            logger.info(
                "memory_versioning: %s is %d bytes (> %d soft cap) — consider splitting",
                file_name, len(content), SOFT_CAP_BYTES,
            )
        self._append_index(row)
        return memver

    # -- read ------------------------------------------------------------------
    def list_versions(self, file_name: str) -> list[dict]:
        """All versions of ``file_name``, NEWEST FIRST."""
        rows = [r for r in self._read_index() if r.get("file") == str(file_name)]
        rows.reverse()
        return rows

    def restore(self, memver: str) -> Optional[bytes]:
        """Return the snapshotted bytes for ``memver`` (point-in-time recovery), or None."""
        for r in self._read_index():
            if r.get("memver") == memver:
                snap = self.snaps / f"{r['sha256']}.snap"
                if snap.exists():
                    return snap.read_bytes()
                return None
        return None

    # -- redact ----------------------------------------------------------------
    def redact(self, memver: str, pattern: str) -> bool:
        """Scrub bytes matching ``pattern`` from ``memver``'s snapshot; keep the audit row.

        The snapshot is content-addressed and may be shared by other versions, so we
        re-point THIS version's row to a fresh redacted snapshot rather than mutating the
        shared blob (which would silently redact unrelated versions). Returns True if a
        change was made.
        """
        rows = self._read_index()
        target = next((r for r in rows if r.get("memver") == memver), None)
        if target is None:
            return False
        snap = self.snaps / f"{target['sha256']}.snap"
        if not snap.exists():
            return False
        original = snap.read_bytes()
        try:
            rx = re.compile(pattern.encode("utf-8"))
        except re.error:
            return False
        scrubbed = rx.sub(_REDACTION, original)
        if scrubbed == original:
            return False
        new_sha = hashlib.sha256(scrubbed).hexdigest()
        self._write_snapshot(new_sha, scrubbed)
        # Rewrite the index, re-pointing only this row + flagging it redacted.
        for r in rows:
            if r.get("memver") == memver:
                r["sha256"] = new_sha
                r["redacted"] = True
        self._rewrite_index(rows)
        return True

    # -- internals -------------------------------------------------------------
    def _write_snapshot(self, sha: str, content: bytes) -> None:
        snap = self.snaps / f"{sha}.snap"
        if snap.exists():
            return  # content-addressed: identical bytes stored once
        _atomic_write_bytes(snap, content)

    def _latest_memver(self, file_name: str) -> Optional[str]:
        latest = None
        for r in self._read_index():
            if r.get("file") == str(file_name):
                latest = r.get("memver")
        return latest

    def _read_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        out: list[dict] = []
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _append_index(self, row: dict) -> None:
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")

    def _rewrite_index(self, rows: list[dict]) -> None:
        text = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        _atomic_write_bytes(self.index_path, text.encode("utf-8"))


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".memver-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def default_versions_root() -> Path:
    """``$HERMES_HOME/memories/.versions``."""
    try:
        from tools.memory_tool import get_memory_dir

        return get_memory_dir() / ".versions"
    except Exception:  # noqa: BLE001
        base = os.environ.get("HERMES_HOME")
        root = Path(base) if base else Path.home() / ".hermes"
        return root / "memories" / ".versions"
