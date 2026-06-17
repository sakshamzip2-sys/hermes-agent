"""MEMORY.md / DREAMS.md I/O for the dreaming plugin.

Promotion appends a new ``§``-delimited entry to the profile's ``MEMORY.md`` —
the same file and delimiter the built-in ``memory`` tool uses, so promoted facts
show up in the agent's memory exactly like hand-curated ones. Holding appends to
``DREAMS.md`` (a lower-confidence pen) with a FIFO byte cap.

We import the canonical ``ENTRY_DELIMITER`` and memory-dir helper from
``tools.memory_tool`` so the format can never drift from core. If that import is
unavailable (e.g. a standalone unit test importing only this module), we fall
back to the documented literals and ``$HERMES_HOME/memories``.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger("hermes.plugins.dreaming.memory_io")

try:  # Stay in lockstep with core's format.
    from tools.memory_tool import ENTRY_DELIMITER, get_memory_dir
except Exception:  # noqa: BLE001 — standalone/test fallback
    ENTRY_DELIMITER = "\n§\n"

    def get_memory_dir() -> Path:  # type: ignore[misc]
        home = os.environ.get("HERMES_HOME")
        base = Path(home) if home else Path.home() / ".hermes"
        return base / "memories"

def _memory_path() -> Path:
    return get_memory_dir() / "MEMORY.md"


def _dreams_path() -> Path:
    return get_memory_dir() / "DREAMS.md"


def _read_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".dream-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_memory_entries() -> list[str]:
    """Current MEMORY.md entries (raw, including any dated marker)."""
    return _read_entries(_memory_path())


def read_memory_facts() -> list[str]:
    """MEMORY.md entries with the ``(dreamed DATE)`` marker stripped.

    Used as the diversity-gate corpus so the dated prefix tokens don't dilute
    the similarity comparison against marker-less candidate facts.
    """
    return [_strip_marker(e) for e in _read_entries(_memory_path())]


def _dreamed_marker(text: str) -> str:
    today = _dt.date.today().isoformat()
    return f"(dreamed {today}) {text}"


def promote(content: str) -> None:
    """Append *content* as a new MEMORY.md entry (dated, deduplicated)."""
    content = content.strip()
    if not content:
        return
    path = _memory_path()
    entries = _read_entries(path)
    # Dedup: skip if the same fact (ignoring our dated prefix) already exists.
    bare = {_strip_marker(e) for e in entries}
    if content in bare:
        logger.debug("dreaming: skipping promote of duplicate fact")
        return
    entries.append(_dreamed_marker(content))
    _atomic_write(path, ENTRY_DELIMITER.join(entries))


def promote_raw(content: str) -> None:
    """Append *content* VERBATIM as a MEMORY.md entry (no added marker, deduped).

    Used by the cross-feed importer, which supplies its own complete
    provenance-bearing marker — ``(dreamed YYYY-MM-DD · source#id · conf=…) …`` —
    so we must NOT prepend a second ``(dreamed …)`` prefix the way :func:`promote`
    does. Dedup is by the bare fact text, consistent with :func:`promote`.
    """
    content = content.strip()
    if not content:
        return
    path = _memory_path()
    entries = _read_entries(path)
    bare_existing = {_strip_marker(e) for e in entries}
    if _strip_marker(content) in bare_existing or content in entries:
        logger.debug("dreaming: skipping promote_raw of duplicate fact")
        return
    entries.append(content)
    _atomic_write(path, ENTRY_DELIMITER.join(entries))


def _strip_marker(entry: str) -> str:
    """Remove a leading ``(dreamed YYYY-MM-DD) `` marker for dedup comparison."""
    if entry.startswith("(dreamed ") and ") " in entry:
        return entry.split(") ", 1)[1].strip()
    return entry.strip()


def replace(old_text: str, new_text: str) -> bool:
    """Replace an existing MEMORY.md entry in place (SUPERSEDE). True if replaced."""
    new_text = new_text.strip()
    if not new_text:
        return False
    path = _memory_path()
    entries = _read_entries(path)
    target_bare = _strip_marker(old_text.strip())
    for i, entry in enumerate(entries):
        if _strip_marker(entry) == target_bare or entry.strip() == old_text.strip():
            entries[i] = _dreamed_marker(new_text)
            _atomic_write(path, ENTRY_DELIMITER.join(entries))
            return True
    return False


def hold(content: str, max_bytes: int) -> None:
    """Append *content* to DREAMS.md with FIFO eviction past *max_bytes*."""
    content = content.strip()
    if not content:
        return
    path = _dreams_path()
    entries = _read_entries(path)
    today = _dt.date.today().isoformat()
    entries.append(f"- {today}: {content}")
    # FIFO-evict oldest entries until under the byte cap.
    while len(ENTRY_DELIMITER.join(entries).encode("utf-8")) > max_bytes and len(entries) > 1:
        entries.pop(0)
    _atomic_write(path, ENTRY_DELIMITER.join(entries))


def read_dreams_entries() -> list[str]:
    """Raw DREAMS.md entries (``- DATE: fact``)."""
    return _read_entries(_dreams_path())


def _strip_dreams_prefix(entry: str) -> str:
    """Strip a leading ``- YYYY-MM-DD: `` prefix to recover the bare fact."""
    m = re.match(r"^-\s*\d{4}-\d{2}-\d{2}:\s*(.*)$", entry.strip(), re.DOTALL)
    return m.group(1).strip() if m else entry.strip()


def read_dreams_facts() -> list[str]:
    """DREAMS.md held facts with the date prefix stripped (for re-scoring)."""
    return [_strip_dreams_prefix(e) for e in _read_entries(_dreams_path())]


def write_dreams_facts(facts: list[str], max_bytes: int) -> None:
    """Rewrite DREAMS.md from *facts* (date-stamped), honouring the byte cap."""
    today = _dt.date.today().isoformat()
    entries = [f"- {today}: {f.strip()}" for f in facts if f.strip()]
    while entries and len(ENTRY_DELIMITER.join(entries).encode("utf-8")) > max_bytes and len(entries) > 1:
        entries.pop(0)
    path = _dreams_path()
    if not entries:
        if path.exists():
            _atomic_write(path, "")
        return
    _atomic_write(path, ENTRY_DELIMITER.join(entries))
