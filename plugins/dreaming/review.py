"""Review-first + rollback for dreaming promotions (HMAC-chained, tamper-evident).

Ported from OpenComputer v1 (``evolution/dreaming_review.py``) into the v2 dreaming
plugin. When ``dreaming.review_mode`` is on, every gate-passing candidate is QUEUED into
``pending_promotions.json`` instead of being written straight to MEMORY.md; an operator
then accepts / rejects / rolls back individual entries via ``hermes dream review``.

This is the non-destructive "review before commit" half of the owner-locked write posture
(the other half — immutable versioning — is :mod:`agent.memory_versioning`). The HMAC chain
makes the queue + rollback log tamper-evident: editing any prior row breaks
:func:`verify_chain`.

Rollback writes a ``# REVOKED <ts> <id>: <text>`` marker into MEMORY.md;
:func:`strip_revoked_lines` filters those out before the agent prompt sees the file, while
audit surfaces keep them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final, Optional

logger = logging.getLogger("hermes.plugins.dreaming.review")

GENESIS_HMAC: Final[str] = "0" * 64
REVOKED_PREFIX: Final[str] = "# REVOKED"
_REVOKED_LINE_RE = re.compile(r"^# REVOKED.*$", flags=re.MULTILINE)


# ─── HMAC key resolution ──────────────────────────────────────────
def _resolve_hmac_key(home: Path) -> bytes:
    """Stable per-home HMAC key at ``<home>/.review_hmac_key`` (created 0600 if absent)."""
    key_path = home / ".review_hmac_key"
    if key_path.exists():
        try:
            return bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError) as exc:
            logger.warning("review hmac key unreadable (%s); regenerating", exc)
    new_key = os.urandom(32)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_key.hex(), encoding="utf-8")
        os.chmod(key_path, 0o600)
    except OSError as exc:
        logger.warning("could not persist review hmac key (%s); chain resets next run", exc)
    return new_key


def _hmac_hex(key: bytes, body: str) -> str:
    return hmac.new(key, body.encode("utf-8"), hashlib.sha256).hexdigest()


# ─── dataclasses ──────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class PendingPromotion:
    id: str
    text: str
    source_event_id: str
    score: float
    recall_count: int
    diversity_score: float
    created_ts_ns: int
    hmac_prev: str
    hmac_self: str
    old_text: Optional[str] = None
    """SUPERSEDE entries: the existing MEMORY.md entry this would REPLACE on accept.
    None for a plain promotion (accept appends). Excluded from the HMAC body when None so
    older queues + plain promotions verify byte-for-byte."""


@dataclass(frozen=True, slots=True)
class RollbackEntry:
    id: str
    memory_id: str
    reverted_text: str
    ts_ns: int
    hmac_prev: str
    hmac_self: str


@dataclass
class ReviewState:
    items: list[PendingPromotion] = field(default_factory=list)
    rollback_log: list[RollbackEntry] = field(default_factory=list)
    hmac_tail: str = GENESIS_HMAC


# ─── state file IO ────────────────────────────────────────────────
def _state_path(home: Path) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    return home / "pending_promotions.json"


def _safe_subset(d: dict[str, Any], cls: type) -> dict[str, Any]:
    allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return {k: d[k] for k in allowed if k in d}


def load_state(home: Path) -> ReviewState:
    path = _state_path(home)
    if not path.exists():
        return ReviewState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("pending_promotions.json unreadable (%s); starting empty", exc)
        return ReviewState()
    items = [PendingPromotion(**_safe_subset(d, PendingPromotion))
             for d in raw.get("items", []) if isinstance(d, dict)]
    rollbacks = [RollbackEntry(**_safe_subset(d, RollbackEntry))
                 for d in raw.get("rollback_log", []) if isinstance(d, dict)]
    return ReviewState(items=items, rollback_log=rollbacks,
                       hmac_tail=str(raw.get("hmac_tail", GENESIS_HMAC)))


def save_state(home: Path, state: ReviewState) -> None:
    path = _state_path(home)
    payload = {
        "items": [asdict(it) for it in state.items],
        "rollback_log": [asdict(r) for r in state.rollback_log],
        "hmac_tail": state.hmac_tail,
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ─── queue + remove + rollback ────────────────────────────────────
def queue_pending(
    home: Path, *, text: str, source_event_id: str, score: float,
    recall_count: int, diversity_score: float, old_text: Optional[str] = None,
    now_ns: Optional[int] = None, key: Optional[bytes] = None,
) -> PendingPromotion:
    key = key or _resolve_hmac_key(home)
    state = load_state(home)
    ts_ns = int(now_ns if now_ns is not None else time.time_ns())
    pid = str(uuid.uuid4())
    body_fields: dict[str, Any] = {
        "kind": "pending", "id": pid, "text": text, "source_event_id": source_event_id,
        "score": score, "recall_count": recall_count, "diversity_score": diversity_score,
        "ts": ts_ns, "prev": state.hmac_tail,
    }
    if old_text is not None:
        body_fields["old_text"] = old_text
    new_tail = _hmac_hex(key, json.dumps(body_fields, sort_keys=True))
    promo = PendingPromotion(
        id=pid, text=text, source_event_id=source_event_id, score=score,
        recall_count=recall_count, diversity_score=diversity_score, created_ts_ns=ts_ns,
        hmac_prev=state.hmac_tail, hmac_self=new_tail, old_text=old_text,
    )
    state.items.append(promo)
    state.hmac_tail = new_tail
    save_state(home, state)
    return promo


def remove_pending(home: Path, *, promotion_id: str) -> Optional[PendingPromotion]:
    state = load_state(home)
    for i, it in enumerate(state.items):
        if it.id == promotion_id:
            popped = state.items.pop(i)
            save_state(home, state)
            return popped
    return None


def record_rollback(
    home: Path, *, memory_id: str, reverted_text: str,
    now_ns: Optional[int] = None, key: Optional[bytes] = None,
) -> RollbackEntry:
    key = key or _resolve_hmac_key(home)
    state = load_state(home)
    ts_ns = int(now_ns if now_ns is not None else time.time_ns())
    rid = str(uuid.uuid4())
    body = json.dumps({
        "kind": "rollback", "id": rid, "memory_id": memory_id,
        "reverted_text": reverted_text, "ts": ts_ns, "prev": state.hmac_tail,
    }, sort_keys=True)
    new_tail = _hmac_hex(key, body)
    entry = RollbackEntry(id=rid, memory_id=memory_id, reverted_text=reverted_text,
                          ts_ns=ts_ns, hmac_prev=state.hmac_tail, hmac_self=new_tail)
    state.rollback_log.append(entry)
    state.hmac_tail = new_tail
    save_state(home, state)
    return entry


# ─── HMAC chain verification ──────────────────────────────────────
def verify_chain(home: Path, *, key: Optional[bytes] = None) -> bool:
    """Replay items + rollbacks in arrival order; True iff every hmac_self matches."""
    key = key or _resolve_hmac_key(home)
    state = load_state(home)
    all_entries: list[tuple[str, Any]] = []
    for it in state.items:
        all_entries.append(("pending", it))
    for r in state.rollback_log:
        all_entries.append(("rollback", r))
    all_entries.sort(key=lambda pair: pair[1].created_ts_ns if pair[0] == "pending" else pair[1].ts_ns)

    tail = GENESIS_HMAC
    for kind, entry in all_entries:
        if entry.hmac_prev != tail:
            return False
        if kind == "pending":
            body_fields: dict[str, Any] = {
                "kind": "pending", "id": entry.id, "text": entry.text,
                "source_event_id": entry.source_event_id, "score": entry.score,
                "recall_count": entry.recall_count, "diversity_score": entry.diversity_score,
                "ts": entry.created_ts_ns, "prev": tail,
            }
            if entry.old_text is not None:
                body_fields["old_text"] = entry.old_text
            body = json.dumps(body_fields, sort_keys=True)
        else:
            body = json.dumps({
                "kind": "rollback", "id": entry.id, "memory_id": entry.memory_id,
                "reverted_text": entry.reverted_text, "ts": entry.ts_ns, "prev": tail,
            }, sort_keys=True)
        if _hmac_hex(key, body) != entry.hmac_self:
            return False
        tail = entry.hmac_self
    return tail == state.hmac_tail


# ─── REVOKED-marker helpers ───────────────────────────────────────
def strip_revoked_lines(text: str) -> str:
    """Remove every ``# REVOKED ...`` line. Idempotent; collapses created blank runs."""
    if REVOKED_PREFIX not in text:
        return text
    stripped = _REVOKED_LINE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", stripped)


def format_revoked_marker(*, memory_id: str, reverted_text: str, ts_ns: int) -> str:
    """Build the single-line audit marker appended to MEMORY.md on rollback."""
    import datetime as _dt

    iso = _dt.datetime.fromtimestamp(ts_ns / 1e9, tz=_dt.UTC).date().isoformat()
    one_line = " ".join(reverted_text.strip().split())
    return f"\n# REVOKED {iso} {memory_id}: {one_line}\n"
