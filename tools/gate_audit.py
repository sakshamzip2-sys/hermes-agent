"""Structured audit log for permission-gate decisions.

The permission gate (``tools/approval.py`` + ``tools/permission_rules.py``)
historically logged blocks via ``logger.warning`` — useful for humans tailing a
log, but not a structured, queryable record of *every* allow/deny decision.

This module adds an append-only JSONL audit trail. Each decision is one line:

    {"ts": "...", "action": "terminal", "verdict": "allowed|blocked|ask",
     "reason": "...", "command": "...", "env_type": "local", "session": "..."}

Writes are best-effort and fully isolated: a logging failure must NEVER break or
delay command execution. The log lives under ``$HERMES_HOME/logs/`` so it is
per-profile and excluded from skill/context scanning.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_WRITE_LOCK = threading.Lock()
_MAX_FIELD_CHARS = 2000  # cap command/reason so a huge heredoc can't bloat the log
_MAX_LOG_BYTES = 10 * 1024 * 1024  # rotate at 10 MB so a 24/7 daemon can't grow it forever

# The log records command text, which can carry inline secrets (mysql -p<pw>,
# curl -H "Authorization: Bearer ...", KEY=secret env assignments, creds in URLs).
# Redact those shapes BEFORE writing so the audit trail is not itself a secret
# store. Patterns are conservative — they mask the value, keep the shape.
_REDACTIONS = [
    # KEY=value where the key name looks secret-bearing
    (re.compile(r'(\b\w*(?:token|secret|password|passwd|api[_-]?key|access[_-]?key|auth)\w*\s*=\s*)(\S+)', re.IGNORECASE), r'\1<redacted>'),
    # -p<pw> / --password <pw> / --password=<pw>
    (re.compile(r'(--?password[=\s]+)(\S+)', re.IGNORECASE), r'\1<redacted>'),
    (re.compile(r'(\bmysql\b[^\n]*?\s-p)(\S+)', re.IGNORECASE), r'\1<redacted>'),
    # Authorization: Bearer / Basic <token>
    (re.compile(r'(authorization:\s*(?:bearer|basic)\s+)(\S+)', re.IGNORECASE), r'\1<redacted>'),
    # credentials embedded in a URL: scheme://user:pass@host
    (re.compile(r'(\w+://[^\s:/@]+:)([^\s@]+)(@)'), r'\1<redacted>\3'),
    # long base64/hex-ish tokens (>=24 chars) as bare args
    (re.compile(r'\b([A-Za-z0-9+/_-]{32,})\b'), '<redacted-token>'),
]


def _redact(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    out = text
    for pat, repl in _REDACTIONS:
        out = pat.sub(repl, out)
    return out


def _audit_path() -> Optional[str]:
    """Return the audit log path under HERMES_HOME/logs, creating the dir 0o700."""
    try:
        from hermes_constants import get_hermes_home

        log_dir = os.path.join(str(get_hermes_home()), "logs")
        os.makedirs(log_dir, exist_ok=True)
        try:
            os.chmod(log_dir, 0o700)  # the log holds command history — owner-only
        except OSError:
            pass
        return os.path.join(log_dir, "gate_decisions.jsonl")
    except Exception:
        return None


def _truncate(value: object) -> object:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + f"...[+{len(value) - _MAX_FIELD_CHARS} chars]"
    return value


def _rotate_if_needed(path: str) -> None:
    """Rotate the log to ``.1`` once it exceeds the size cap (keep one backup)."""
    try:
        if os.path.exists(path) and os.path.getsize(path) >= _MAX_LOG_BYTES:
            backup = path + ".1"
            try:
                os.replace(path, backup)  # atomic; overwrites a prior .1
            except OSError:
                pass
    except OSError:
        pass


def record_decision(
    *,
    action: str,
    verdict: str,
    reason: Optional[str] = None,
    command: Optional[str] = None,
    env_type: Optional[str] = None,
    rule: Optional[str] = None,
    session: Optional[str] = None,
) -> None:
    """Append one gate decision to the structured audit log (best-effort).

    Args:
        action: The gated action class, e.g. ``terminal`` / ``execute_code``.
        verdict: ``allowed`` | ``blocked`` | ``ask`` | ``hardline``.
        reason: Human-readable reason (the matched pattern description, etc.).
        command: The command/code being gated (truncated).
        env_type: The resolved execution backend (local/docker/...).
        rule: The rule or guard that produced the verdict.
        session: Session key, when available.
    """
    if os.getenv("HERMES_GATE_AUDIT", "on").strip().lower() in {"0", "off", "false", "no"}:
        return
    path = _audit_path()
    if not path:
        return
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "verdict": verdict,
            "reason": _truncate(reason) if reason else None,
            "rule": rule,
            # Redact inline secrets, THEN truncate. The log is a forensic record,
            # not a secret store.
            "command": _truncate(_redact(command)) if command else None,
            "env_type": env_type,
            "session": session,
        }
        line = json.dumps(entry, ensure_ascii=False)
        with _WRITE_LOCK:
            _rotate_if_needed(path)
            # Open with owner-only perms (0o600): the log holds command history.
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                # Belt-and-suspenders: if the file pre-existed with looser perms,
                # tighten it.
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
                os.write(fd, (line + "\n").encode("utf-8"))
            finally:
                os.close(fd)
    except Exception as exc:  # pragma: no cover - logging must never break exec
        logger.debug("gate audit log write failed: %s", exc)


def _verdict_from_result(result: dict) -> str:
    """Map an approval-result dict to an audit verdict string.

    Distinguishes a command the user was *prompted on and approved* from one
    that was *auto-allowed* — the whole point of a forensic trail is to know
    when a human waved a flagged command through.
    """
    if not isinstance(result, dict):
        return "unknown"
    if result.get("hardline"):
        return "hardline"
    if result.get("approved") is False:
        return "blocked"
    # Approved after a user was prompted (the approval path sets user_approved /
    # carries a description of what was flagged) — record it distinctly.
    if result.get("user_approved") or result.get("description"):
        return "user-approved"
    if result.get("message"):
        return "allowed-with-note"
    return "allowed"
