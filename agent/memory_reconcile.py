"""Reconcile write engine (Decision C, PHASE3) - LOCAL-FIRST.

This is the background write path of the locked memory design: a deterministic,
rule-based reconcile engine that takes EXTRACTED fact candidate strings and,
for each one:

  (a) wraps it untrusted + runs ``scan_for_threats(scope="strict")`` - on a hit
      the candidate is SKIPPED (never stored as a trusted fact, req #11);
  (b) redacts secrets / credentials / high-sensitivity PII via
      ``tools.memory_redaction.redact`` BEFORE any store write (req #8);
  (c) retrieves similar existing facts via the store's READ-ONLY path
      (``search_facts_readonly`` with OR-expansion; HRR cosine when numpy is
      present) to decide the operation;
  (d) decides ADD / UPDATE (supersede, recency-wins) / NOOP, and respects a
      store / not-store policy (transient / low-signal / empty candidates skip).

It is LOCAL-FIRST: only the holographic FTS5 plane is written here. The remote
Honcho / GBrain routing is a QUEUED stub this wave (``_queue_remote``), recorded
in the op-queue with ``op="QUEUE_REMOTE"`` but not dispatched.

Idempotence (req #5) is provided by a durable op-queue table ``reconcile_ops``
in the store DB, keyed by a stable ``op_id`` = hash(source_store + content + op).
Re-running ``reconcile`` on the same candidates finds the op already applied and
returns NOOP for it, so the engine is safe to re-run after a crash / restart.

The engine is MODEL-OPTIONAL: ``model`` defaults to ``None`` and the rule-based
path runs with no LLM, which is what the tests exercise. A real extraction LLM
can later feed candidate strings in; the reconcile contract is identical.

No em dashes (house rule). Pyright-clean.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from tools.memory_redaction import redact as _redact
from tools.memory_redaction import (
    scan_destructive_advice as _scan_destructive_advice,
)
from tools.memory_redaction import (
    scan_supplementary_injection as _scan_supplementary_injection,
)

# Threat scan (the per-candidate injection fence). Imported defensively so the
# module stays importable in trimmed environments; the fallback is an inert
# no-op (never silently-permissive in the live tree, where the real function
# always imports).
try:
    from tools.threat_patterns import scan_for_threats as _scan_for_threats
except Exception:  # pragma: no cover - defensive import only
    def _scan_for_threats(content: str, scope: str = "context") -> List[str]:
        return []

# OR-expansion reused from the store so retrieve-similar matches the read path
# (FTS5 implicit-AND fix; 0.62 NL vs 1.00 OR). Defensive import with a local
# fallback so the engine never hard-depends on the plugin import path.
try:
    from plugins.memory.holographic.store import _or_expand_query as _or_expand
except Exception:  # pragma: no cover - defensive import only
    def _or_expand(query: str) -> str:
        return query

# Namespace-integrity guard (GAP-7 layer 2). A remote-origin write MUST route to
# a remote namespace and is structurally forbidden from a self namespace. The
# defensive fallback re-implements the same rule so the guard can never silently
# become a no-op if the plugin import path is trimmed. The fallback symbols are
# defined FIRST (unconditionally typed) and only OVERRIDDEN by the real store
# implementation when the import succeeds, so there is one stable type for each.
_FALLBACK_SELF_NS = frozenset({"orchestrator/self", "orchestrator/shared"})


class RemoteNamespaceViolation(ValueError):
    """Fallback mirror of the store's guard exception (see that module).

    Overridden by the store's class when the plugin import path is available, so
    a remote-ingest seam can catch the same type whether the real store module
    is importable or not.
    """


def assert_not_self_namespace(source_store: "str | None") -> str:
    """Fallback namespace-integrity guard (see the store module's docstring)."""
    if source_store is None or source_store in _FALLBACK_SELF_NS:
        raise RemoteNamespaceViolation(
            "remote-origin write may not target a self-generated namespace "
            f"(got {source_store!r})"
        )
    return source_store


try:  # Prefer the real store implementation when importable.
    from plugins.memory.holographic.store import (
        RemoteNamespaceViolation as _StoreRemoteNamespaceViolation,
    )
    from plugins.memory.holographic.store import (
        assert_not_self_namespace as _store_assert_not_self_namespace,
    )

    RemoteNamespaceViolation = _StoreRemoteNamespaceViolation  # type: ignore[misc,assignment]
    assert_not_self_namespace = _store_assert_not_self_namespace
except Exception:  # pragma: no cover - defensive import only; keep the fallbacks
    pass

# HRR cosine for semantic near-dup retrieve-similar. Numpy may be ABSENT, in
# which case _HRR_AVAILABLE is False and the engine uses FTS5 + text-hash only.
try:
    from plugins.memory.holographic import holographic as _hrr  # type: ignore
    _HRR_AVAILABLE = bool(getattr(_hrr, "_HAS_NUMPY", False))
except Exception:  # pragma: no cover - defensive import only
    _hrr = None  # type: ignore[assignment]
    _HRR_AVAILABLE = False


# ===========================================================================
# Op record
# ===========================================================================

# The four reconcile operations plus the remote-queue stub.
OP_ADD = "ADD"
OP_UPDATE = "UPDATE"
OP_NOOP = "NOOP"
OP_SKIP = "SKIP"            # threat-scanned out (req #11) or store/not-store reject
OP_REVIEW = "REVIEW"       # destructive "best practice" advice (MemoryGraft): held, not silently stored
OP_QUEUE_REMOTE = "QUEUE_REMOTE"  # routed to Honcho/GBrain, stubbed this wave
OP_REJECT_SELF_NS = "REJECT_SELF_NS"  # GAP-7: remote-origin write aimed at a self namespace


@dataclass
class OpRecord:
    """One reconcile decision, returned to the caller and recorded durably.

    Attributes
    ----------
    op:
        One of ADD / UPDATE / NOOP / SKIP / REVIEW / QUEUE_REMOTE.
    ext_key:
        The stable external key of the affected fact (the NEW fact for ADD /
        UPDATE; the matched existing fact for NOOP; ``None`` for SKIP / a remote
        queue stub).
    content:
        The REDACTED content that was (or would be) stored. For SKIP this is the
        reason-tagged content so the caller can see what was rejected and why.
    op_id:
        The stable idempotency key (hash of source_store + content + op).
    reason:
        Short machine-readable reason (e.g. ``"threat:<pid>"``, ``"transient"``,
        ``"empty"``, ``"duplicate"``, ``"superseded:<old_ext_key>"``).
    superseded_ext_key:
        For UPDATE, the ext_key of the invalidated old fact.
    """

    op: str
    ext_key: Optional[str]
    content: str
    op_id: str
    reason: str = ""
    superseded_ext_key: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# ===========================================================================
# Store / not-store policy (MEMORY-POLICY section 2)
# ===========================================================================

# Low-signal openers / acknowledgements that should NOOP, not be stored as
# durable facts. Matched against the normalized candidate.
_LOW_SIGNAL_EXACT = frozenset({
    "ok", "okay", "thanks", "thank you", "sure", "yes", "no", "yep", "nope",
    "got it", "done", "cool", "great", "hi", "hello", "hey", "noted",
})

# Transient-state markers (MEMORY-POLICY: "current mood", "the file I just
# opened", a one-off scratch value). A candidate that is primarily about the
# immediate moment is not a durable fact. Conservative substring set.
_TRANSIENT_RE = re.compile(
    r"\b("
    r"i (?:am|'m) (?:currently |now )?(?:feeling|tired|happy|sad|hungry|bored)"
    r"|the file i (?:just )?opened"
    r"|right now"
    r"|at the moment"
    r"|for now"
    r"|just opened"
    r"|currently looking at"
    r")\b",
    re.IGNORECASE,
)

# A durable fact should carry SOME content. After redaction + stripping, a
# candidate shorter than this (in non-whitespace chars) is treated as empty /
# low-signal and NOOPed.
_MIN_DURABLE_CHARS = 3

_RE_WS = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _RE_WS.sub(" ", (text or "").strip().lower())


def _is_storable(content: str) -> "tuple[bool, str]":
    """Decide whether a (already-redacted) candidate is worth storing.

    Returns ``(storable, reason)``. ``reason`` is set when NOT storable so the
    caller can record why. Implements the MEMORY-POLICY store / not-store rule
    for the deterministic path:
      - empty / whitespace-only -> not storable ("empty")
      - too short to be a durable fact -> not storable ("low_signal")
      - a low-signal acknowledgement -> not storable ("low_signal")
      - a transient-state statement -> not storable ("transient")
      - otherwise storable.
    """
    norm = _normalize(content)
    if not norm or len(norm.replace(" ", "")) < _MIN_DURABLE_CHARS:
        return False, "empty"
    if norm in _LOW_SIGNAL_EXACT:
        return False, "low_signal"
    if _TRANSIENT_RE.search(content):
        return False, "transient"
    return True, ""


# ===========================================================================
# Subject extraction for same-subject UPDATE detection
# ===========================================================================

# A knowledge-UPDATE ("the port is 8000" -> "the port is 8642") is a
# same-SUBJECT fact with a DIFFERENT value. We detect "same subject" by the
# leading clause up to a copula (is / are / was / were / = / :), normalized.
# This is deterministic and LLM-free, matching the test contract.
_SUBJECT_SPLIT_RE = re.compile(
    r"\s+(?:is|are|was|were|will be|equals?|=|:)\s+",
    re.IGNORECASE,
)


def _subject_key(content: str) -> Optional[str]:
    """Return the normalized SUBJECT clause of a fact, or None if it has none.

    "The port is 8642" -> "the port". "my editor = vim" -> "my editor". A fact
    with no copula has no extractable subject (returns None) and can only ADD or
    exact-dedup, never same-subject-supersede.
    """
    parts = _SUBJECT_SPLIT_RE.split(content, maxsplit=1)
    if len(parts) < 2:
        return None
    subject = _normalize(parts[0])
    return subject or None


# ===========================================================================
# Durable op-queue (idempotence, req #5)
# ===========================================================================

_OPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS reconcile_ops (
    op_id        TEXT PRIMARY KEY,
    op           TEXT NOT NULL,
    source_store TEXT NOT NULL,
    content      TEXT NOT NULL,
    ext_key      TEXT,
    reason       TEXT DEFAULT '',
    superseded   TEXT,
    applied_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _op_id(source_store: str, content: str, op: str) -> str:
    """Stable idempotency key = sha256(source_store|content|op) hex digest.

    Content is normalized so trivial whitespace differences map to the same id.
    The op is part of the key so an ADD and a later UPDATE of the same content
    are distinct ops (an UPDATE is keyed by the NEW content, which differs from
    the old ADD's content, so the two never collide anyway; including op keeps
    the key honest).
    """
    norm = _normalize(content)
    raw = f"{source_store}\x1f{norm}\x1f{op}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ensure_ops_table(conn: sqlite3.Connection) -> None:
    """Create the reconcile_ops table if missing. Idempotent."""
    conn.executescript(_OPS_SCHEMA)
    conn.commit()


def _op_already_applied(conn: sqlite3.Connection, op_id: str) -> "sqlite3.Row | None":
    row = conn.execute(
        "SELECT op_id, op, ext_key, content, reason, superseded "
        "FROM reconcile_ops WHERE op_id = ?",
        (op_id,),
    ).fetchone()
    return row


def _record_op(conn: sqlite3.Connection, rec: OpRecord, source_store: str) -> None:
    """Durably record an applied op. INSERT OR IGNORE so a re-apply is a no-op."""
    conn.execute(
        """
        INSERT OR IGNORE INTO reconcile_ops
            (op_id, op, source_store, content, ext_key, reason, superseded)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            rec.op_id,
            rec.op,
            source_store,
            rec.content,
            rec.ext_key,
            rec.reason,
            rec.superseded_ext_key,
        ),
    )
    conn.commit()


# ===========================================================================
# Retrieve-similar
# ===========================================================================

# HRR cosine threshold above which two facts are treated as the SAME fact
# (near-duplicate paraphrase). Mirrors the merge layer's dedup threshold.
_HRR_DUP_THRESHOLD = 0.92


def _retrieve_similar(
    store: Any,
    content: str,
    *,
    source_store: str,
    limit: int = 10,
) -> List[dict]:
    """Read-only retrieve of existing facts similar to ``content``.

    Uses ``search_facts_readonly`` with internal OR-expansion (the read path's
    NL->OR fix) so a paraphrased candidate still finds its near-dup. Namespace
    filtered to ``source_store`` so reconcile only compares within the plane it
    writes to. Pure read: never writes, never increments retrieval_count.
    """
    try:
        rows = store.search_facts_readonly(
            content,
            min_trust=0.0,
            limit=limit,
            or_expand=True,
            source_store=source_store,
        )
    except TypeError:
        # Older store signature without source_store/or_expand kwargs: fall back
        # to the OR-expanded query passed positionally.
        rows = store.search_facts_readonly(_or_expand(content), limit=limit)
    return list(rows or [])


def _hrr_vector_for(text: str) -> Any:
    """Best-effort HRR phase vector for text, or None when numpy is absent."""
    if not _HRR_AVAILABLE or _hrr is None:
        return None
    try:
        return _hrr.encode_text(text)
    except Exception:
        return None


def _find_exact_or_neardup(
    content: str,
    similar: Sequence[dict],
) -> "dict | None":
    """Return an existing fact that is the SAME fact as ``content``, or None.

    Same-fact means: identical normalized text (exact dedup), OR an HRR-cosine
    near-duplicate above threshold when vectors are available. This drives the
    NOOP decision (the fact already exists).
    """
    norm = _normalize(content)
    for row in similar:
        if _normalize(str(row.get("content", ""))) == norm:
            return row

    if _HRR_AVAILABLE:
        vec = _hrr_vector_for(content)
        if vec is not None:
            for row in similar:
                blob = row.get("hrr_vector")
                ovec = None
                if isinstance(blob, (bytes, bytearray)) and _hrr is not None:
                    try:
                        ovec = _hrr.bytes_to_phases(bytes(blob))
                    except Exception:
                        ovec = None
                if ovec is None:
                    ovec = _hrr_vector_for(str(row.get("content", "")))
                if ovec is None:
                    continue
                try:
                    if _hrr.similarity(vec, ovec) >= _HRR_DUP_THRESHOLD:  # type: ignore[union-attr]
                        return row
                except Exception:
                    continue
    return None


def _find_same_subject_different_value(
    content: str,
    similar: Sequence[dict],
) -> "dict | None":
    """Return an existing fact with the SAME subject but a DIFFERENT value.

    "The port is 8000" already stored, candidate "The port is 8642" -> the
    stored fact is returned so reconcile supersedes it (recency-wins). A fact
    with no extractable subject returns None (can only ADD / exact-dedup).
    """
    subject = _subject_key(content)
    if subject is None:
        return None
    cand_norm = _normalize(content)
    for row in similar:
        existing = str(row.get("content", ""))
        if _subject_key(existing) == subject and _normalize(existing) != cand_norm:
            return row
    return None


# ===========================================================================
# Remote routing stub (LOCAL-FIRST: queued, not dispatched this wave)
# ===========================================================================

def _route_plane(content: str) -> str:
    """One-plane-per-fact routing hint (MEMORY-POLICY section 1).

    This wave is LOCAL-FIRST: everything reconciled here lands in the
    holographic FTS5 plane. The routing classifier is kept so the QUEUE_REMOTE
    stub is honest about WHERE a fact would go, but no remote dispatch happens.
    Returns one of "holographic" | "honcho" | "gbrain".
    """
    # Deterministic, conservative. Identity / preference / standing-instruction
    # language routes to Honcho; explicit "entity X relates to Y" graph language
    # routes to GBrain; everything else (paths, ids, config, decisions) is an
    # exact/verbatim fact for the holographic plane. This wave only WRITES
    # holographic; honcho/gbrain are queued.
    lowered = content.lower()
    if re.search(r"\b(i prefer|i like|i always|i never|my preference|call me)\b", lowered):
        return "honcho"
    return "holographic"


# ===========================================================================
# Public API
# ===========================================================================

def write_fact_now(
    store: Any,
    content: str,
    *,
    category: str = "general",
    tags: str = "",
    source_store: str = "orchestrator/self",
    redact_first: bool = True,
    self_generated: bool = True,
) -> "str | None":
    """Read-your-writes: hot-INSERT a fact so it is recallable immediately.

    Uses the store's two-phase ``add_fact(defer_enrichment=True)`` hot path: one
    INSERT (content + source_store + tags), NO HRR encode, NO bank rebuild, and
    the AFTER INSERT trigger indexes it in FTS5 so the SAME or NEXT turn can
    recall it via ``search_facts_readonly``. Enrichment is deferred to the
    background dreaming pass.

    Redaction (``redact_first``, default True) runs on the content BEFORE the
    INSERT so a just-stated secret is never persisted raw (req #8). Returns the
    fact's stable ``ext_key`` on success, or ``None`` when the content is empty
    / not storable.

    ``self_generated`` (GAP-7 layer 1, default ``True``) flows to the store's
    signing gate. The default suits this function's normal caller (the
    orchestrator persisting its OWN just-stated fact into ``orchestrator/self``,
    which should be signed). A remote relay must pass ``self_generated=False``
    AND a remote ``source_store`` so relayed content is never self-signed.
    """
    if redact_first:
        content, _ = _redact(content)
    storable, _reason = _is_storable(content)
    if not storable:
        return None
    content = content.strip()
    # Hot INSERT (defer_enrichment): cheap, immediately FTS5-recallable.
    store.add_fact(
        content,
        category=category,
        tags=tags,
        source_store=source_store,
        defer_enrichment=True,
        self_generated=self_generated,
    )
    # Derive the same stable ext_key the store assigns (content-hash UUID).
    try:
        from plugins.memory.holographic.store import _content_ext_key
        return _content_ext_key(content)
    except Exception:  # pragma: no cover - defensive
        return None


def reconcile(
    candidates: Sequence[str],
    store: Any,
    *,
    source_store: str = "orchestrator/self",
    model: Any = None,
) -> List[OpRecord]:
    """Reconcile extracted fact candidates into the store (Decision C).

    For each candidate string, in order:
      1. wrap untrusted + ``scan_for_threats(scope="strict")`` plus the
         supplementary shape scan; on a hit -> SKIP (never stored as a trusted
         fact, req #11);
      1b. destructive-advice fence (MemoryGraft): generalized "best practice"
         advice that encodes a destructive op (force-push / skip-validation /
         disable-auth) is held for REVIEW, not silently stored;
      2. redact secrets / PII via ``tools.memory_redaction.redact`` (req #8);
      3. store / not-store policy (transient / low-signal / empty -> SKIP);
      4. idempotence: if this (source_store, content, op) was already applied,
         return NOOP (durable op-queue, req #5);
      5. retrieve-similar via ``search_facts_readonly`` (OR-expanded; HRR cosine
         when numpy present), namespace-filtered to ``source_store``;
      6. decide:
           - NOOP if a near-identical fact already exists;
           - UPDATE (supersede: add new + invalidate old, recency-wins) if a
             same-subject fact with a DIFFERENT value exists;
           - ADD if novel.
      7. route ONE plane per fact; LOCAL-FIRST writes holographic, queues
         honcho/gbrain (QUEUE_REMOTE stub).

    ``model`` is OPTIONAL and unused on this deterministic path (the caller
    passes candidate strings already; no LLM is required). It is part of the
    signature so a later capable-model extraction step can be slotted in without
    changing the reconcile contract.

    Returns a list of :class:`OpRecord`, one per input candidate, in order.
    """
    conn = _store_conn(store)
    _ensure_ops_table(conn)

    results: List[OpRecord] = []
    for raw in candidates:
        results.append(
            _reconcile_one(raw, store, conn, source_store=source_store)
        )
    return results


def reconcile_remote(
    candidates: Sequence[str],
    store: Any,
    *,
    source_store: str,
    model: Any = None,
) -> List[OpRecord]:
    """Ingest REMOTE-ORIGIN fact candidates into a REMOTE namespace (GAP-7 L2).

    This is the dedicated entry point for any cross-store relay (a future
    Honcho/GBrain importer, the QUEUE_REMOTE drainer) that writes fetched
    upstream content INTO the holographic store. It is the structural backstop
    for the write-side self-signing hole:

      1. ``source_store`` is funneled through
         :func:`plugins.memory.holographic.store.assert_not_self_namespace`
         FIRST. A remote-origin write aimed at ``orchestrator/self`` /
         ``orchestrator/shared`` (or ``None``) is REJECTED outright
         (``OP_REJECT_SELF_NS``), never written, never signed. So a remote relay
         that forgets / forges a self namespace cannot land there.
      2. Every write this path performs passes ``self_generated=False`` to the
         store, so even a future bug that let a self namespace slip past step 1
         would STILL never produce a valid signature (defense in depth: the two
         layers are independent).

    Aside from the namespace gate + the self_generated=False flag this reuses the
    exact same per-candidate pipeline as :func:`reconcile` (threat scan ->
    redaction -> store/not-store -> retrieve-similar -> ADD/UPDATE/NOOP), so
    relayed remote content gets the same safety treatment as self content.

    Returns one :class:`OpRecord` per candidate, in order. If the namespace is a
    self namespace, EVERY candidate returns ``OP_REJECT_SELF_NS`` (the whole
    batch is refused) rather than silently downgrading the namespace, so the
    misroute is loud.
    """
    conn = _store_conn(store)
    _ensure_ops_table(conn)

    # Namespace-integrity gate (layer 2). A self namespace is refused for the
    # whole batch; the violation is recorded per candidate so it is auditable.
    try:
        assert_not_self_namespace(source_store)
    except RemoteNamespaceViolation as exc:
        reason = f"remote_into_self_ns:{source_store}"
        results: List[OpRecord] = []
        for raw in candidates:
            op_id = _op_id(source_store, raw or "", OP_REJECT_SELF_NS)
            rec = OpRecord(
                op=OP_REJECT_SELF_NS,
                ext_key=None,
                content="[REJECTED:remote-into-self-namespace]",
                op_id=op_id,
                reason=reason,
                metadata={"violation": str(exc)},
            )
            _record_op(conn, rec, source_store)
            results.append(rec)
        return results

    results = []
    for raw in candidates:
        results.append(
            _reconcile_one(
                raw, store, conn,
                source_store=source_store,
                self_generated=False,
            )
        )
    return results


def _store_conn(store: Any) -> sqlite3.Connection:
    """Return the store's write connection for the op-queue table.

    The op-queue lives in the SAME DB as the facts (req #3: "a small table
    'reconcile_ops' in the store DB"), so it shares transactional fate with the
    writes it records. MemoryStore exposes ``_conn``; a duck-typed store may
    expose ``conn``.
    """
    conn = getattr(store, "_conn", None)
    if conn is None:
        conn = getattr(store, "conn", None)
    if conn is None:
        raise AttributeError(
            "reconcile: store must expose a sqlite3 connection via _conn or conn"
        )
    return conn


def _reconcile_one(
    raw: str,
    store: Any,
    conn: sqlite3.Connection,
    *,
    source_store: str,
    self_generated: bool = True,
) -> OpRecord:
    """Reconcile a single candidate. See :func:`reconcile` for the contract.

    ``self_generated`` (GAP-7 layer 1) flows straight to the store on every
    write this candidate performs. The self path (:func:`reconcile`) leaves it
    ``True`` (the orchestrator's own facts are signed); the remote path
    (:func:`reconcile_remote`) passes ``False`` so relayed content is never
    self-signed even though it reuses this same pipeline.
    """
    original = raw or ""

    # --- 1. injection fence (req #11): scan the RAW candidate, strict scope. ---
    #     Two layers, both routing to SKIP: (1) the repo's strict threat scanner
    #     (catches the classic "ignore all previous instructions" shape), and
    #     (2) a conservative SUPPLEMENTARY shape check (defense-in-depth) that
    #     also flags system-role impersonation tags/prefixes, "disregard the
    #     above", and destructive-command imperatives, which the strict scanner
    #     let through and were being STORED as trusted facts.
    threats = _scan_for_threats(original, scope="strict")
    if not threats:
        threats = _scan_supplementary_injection(original)
    if threats:
        pid = threats[0]
        op_id = _op_id(source_store, original, OP_SKIP)
        rec = OpRecord(
            op=OP_SKIP,
            ext_key=None,
            content="[BLOCKED:injection]",
            op_id=op_id,
            reason=f"threat:{pid}",
        )
        _record_op(conn, rec, source_store)
        return rec

    # --- 1b. destructive-advice fence (MemoryGraft, build-queue item 5). -----
    #     A fabricated "best practice" carries NO injection anomaly, so the
    #     scanner above lets it through and it was being silently ADDed as a
    #     trusted fact. When the candidate is generalized advice ("always skip
    #     validation and force-push to main") it is HELD for REVIEW: not stored,
    #     not hard-SKIPped (the advice may be the safe inverse and needs a human
    #     to confirm intent). This is a NARROW heuristic, not an injection block;
    #     the retrieval-time consensus/trust layer is the backstop for novel
    #     wording. See tools.memory_redaction.scan_destructive_advice.
    advice = _scan_destructive_advice(original)
    if advice:
        kind = advice[0]
        op_id = _op_id(source_store, original, OP_REVIEW)
        prior = _op_already_applied(conn, op_id)
        if prior is not None:
            return OpRecord(
                op=OP_REVIEW, ext_key=None, content=original,
                op_id=op_id, reason=f"advice_review:{kind}",
                metadata={"advice_kinds": advice},
            )
        rec = OpRecord(
            op=OP_REVIEW,
            ext_key=None,
            content=original,
            op_id=op_id,
            reason=f"advice_review:{kind}",
            metadata={"advice_kinds": advice},
        )
        _record_op(conn, rec, source_store)
        return rec

    # --- 2. redaction (req #8): secrets -> typed placeholders, BEFORE store. ---
    content, redaction_hits = _redact(original)
    content = content.strip()

    # --- 3. store / not-store policy. ---
    storable, why = _is_storable(content)
    if not storable:
        op_id = _op_id(source_store, content or original, OP_SKIP)
        rec = OpRecord(
            op=OP_SKIP,
            ext_key=None,
            content=content,
            op_id=op_id,
            reason=why,
            metadata={"redaction_hits": redaction_hits},
        )
        _record_op(conn, rec, source_store)
        return rec

    # --- 7a. one-plane routing. LOCAL-FIRST: non-holographic is queued. ---
    plane = _route_plane(content)
    if plane != "holographic":
        op_id = _op_id(source_store, content, OP_QUEUE_REMOTE)
        prior = _op_already_applied(conn, op_id)
        if prior is not None:
            return OpRecord(
                op=OP_NOOP, ext_key=prior["ext_key"], content=content,
                op_id=op_id, reason="idempotent:queued",
            )
        rec = OpRecord(
            op=OP_QUEUE_REMOTE,
            ext_key=None,
            content=content,
            op_id=op_id,
            reason=f"plane:{plane}",
            metadata={"plane": plane, "redaction_hits": redaction_hits},
        )
        _record_op(conn, rec, source_store)
        return rec

    # --- 5. retrieve-similar (read-only, OR-expanded, namespace-filtered). ---
    similar = _retrieve_similar(store, content, source_store=source_store)

    # --- 6a. NOOP if a near-identical fact already exists. ---
    existing = _find_exact_or_neardup(content, similar)
    if existing is not None:
        op_id = _op_id(source_store, content, OP_ADD)
        return OpRecord(
            op=OP_NOOP,
            ext_key=str(existing.get("ext_key") or ""),
            content=content,
            op_id=op_id,
            reason="duplicate",
            metadata={"redaction_hits": redaction_hits},
        )

    # --- idempotence: same content ADDed before -> NOOP (durable op-queue). ---
    add_op_id = _op_id(source_store, content, OP_ADD)
    prior_add = _op_already_applied(conn, add_op_id)
    if prior_add is not None:
        return OpRecord(
            op=OP_NOOP,
            ext_key=prior_add["ext_key"],
            content=content,
            op_id=add_op_id,
            reason="idempotent:add",
            metadata={"redaction_hits": redaction_hits},
        )

    # --- 6b. UPDATE (supersede) if a same-subject DIFFERENT-value fact exists. ---
    superseded = _find_same_subject_different_value(content, similar)
    if superseded is not None:
        old_ext_key = str(superseded.get("ext_key") or "")
        update_op_id = _op_id(source_store, content, OP_UPDATE)
        prior_upd = _op_already_applied(conn, update_op_id)
        if prior_upd is not None:
            return OpRecord(
                op=OP_NOOP, ext_key=prior_upd["ext_key"], content=content,
                op_id=update_op_id, reason="idempotent:update",
                superseded_ext_key=old_ext_key,
            )
        new_ext_key = store.supersede(
            old_ext_key,
            content,
            category=str(superseded.get("category") or "general"),
            tags=str(superseded.get("tags") or ""),
            source_store=source_store,
            defer_enrichment=True,
            self_generated=self_generated,
        )
        rec = OpRecord(
            op=OP_UPDATE,
            ext_key=new_ext_key,
            content=content,
            op_id=update_op_id,
            reason=f"superseded:{old_ext_key}",
            superseded_ext_key=old_ext_key,
            metadata={"redaction_hits": redaction_hits},
        )
        _record_op(conn, rec, source_store)
        return rec

    # --- 6c. ADD (novel). Hot INSERT (defer_enrichment) for read-your-writes. ---
    store.add_fact(
        content,
        category="general",
        tags="",
        source_store=source_store,
        defer_enrichment=True,
        self_generated=self_generated,
    )
    new_ext_key = _derive_ext_key(content)
    rec = OpRecord(
        op=OP_ADD,
        ext_key=new_ext_key,
        content=content,
        op_id=add_op_id,
        reason="novel",
        metadata={"redaction_hits": redaction_hits},
    )
    _record_op(conn, rec, source_store)
    return rec


def _derive_ext_key(content: str) -> "str | None":
    """Derive the store's stable content-hash ext_key for ``content``."""
    try:
        from plugins.memory.holographic.store import _content_ext_key
        return _content_ext_key(content)
    except Exception:  # pragma: no cover - defensive
        return None


__all__ = [
    "reconcile",
    "reconcile_remote",
    "write_fact_now",
    "OpRecord",
    "OP_ADD",
    "OP_UPDATE",
    "OP_NOOP",
    "OP_SKIP",
    "OP_REVIEW",
    "OP_QUEUE_REMOTE",
    "OP_REJECT_SELF_NS",
    "RemoteNamespaceViolation",
    "assert_not_self_namespace",
]
