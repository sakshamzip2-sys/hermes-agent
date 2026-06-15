"""Memory writeback — a closed check-in's reply becomes a MEMORY.md entry.

The learning loop: after the user replies to a "how did X go?" check-in, their words
are captured into ``MEMORY.md`` so future turns recall the outcome. Two-hats: we store
the user's own words framed minimally — never operational/agent context.

Writes use the canonical ``ENTRY_DELIMITER`` + memories dir from ``tools.memory_tool``
(falling back to the documented literals for standalone tests), matching core's format.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("hermes.plugins.proactivity.writeback")

try:
    from tools.memory_tool import ENTRY_DELIMITER, get_memory_dir
except Exception:  # noqa: BLE001 — standalone/test fallback
    ENTRY_DELIMITER = "\n§\n"

    def get_memory_dir() -> Path:  # type: ignore[misc]
        home = os.environ.get("HERMES_HOME")
        base = Path(home) if home else Path.home() / ".hermes"
        return base / "memories"

# A check-in reply over this length is almost certainly normal conversation that
# happened to follow a check-in, not an actual reply — don't capture it verbatim.
_MAX_REPLY_CHARS = 600


def _memory_path() -> Path:
    return get_memory_dir() / "MEMORY.md"


def _read_entries(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [e.strip() for e in raw.split(ENTRY_DELIMITER) if e.strip()]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".proact-", suffix=".tmp")
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


def write_checkin_reply(event_title: str, reply_text: str) -> bool:
    """Append the user's check-in reply to MEMORY.md. True if written.

    Returns False (no write) for empty or over-long replies (the latter are
    treated as ordinary conversation, not a check-in answer).
    """
    reply = (reply_text or "").strip()
    title = (event_title or "").strip()
    if not reply or not title:
        return False
    if len(reply) > _MAX_REPLY_CHARS:
        return False

    entry = f"On {title}, the user said: {reply}"
    path = _memory_path()
    entries = _read_entries(path)
    if entry in entries:
        return False
    entries.append(entry)
    _atomic_write(path, ENTRY_DELIMITER.join(entries))
    return True
