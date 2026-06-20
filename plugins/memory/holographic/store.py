"""
SQLite-backed fact store with entity resolution and trust scoring.
Single-user OpenComputer memory store plugin.
"""

import hashlib
import hmac
import logging
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("hermes.plugins.memory.holographic.store")

try:
    from . import holographic as hrr
except ImportError:
    import holographic as hrr  # type: ignore[no-redef]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL UNIQUE,
    category        TEXT DEFAULT 'general',
    tags            TEXT DEFAULT '',
    trust_score     REAL DEFAULT 0.5,
    retrieval_count INTEGER DEFAULT 0,
    helpful_count   INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hrr_vector      BLOB
);

CREATE TABLE IF NOT EXISTS entities (
    entity_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    entity_type TEXT DEFAULT 'unknown',
    aliases     TEXT DEFAULT '',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_entities (
    fact_id   INTEGER REFERENCES facts(fact_id),
    entity_id INTEGER REFERENCES entities(entity_id),
    PRIMARY KEY (fact_id, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_facts_trust    ON facts(trust_score DESC);
CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_entities_name  ON entities(name);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts
    USING fts5(content, tags, content=facts, content_rowid=fact_id);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
END;

CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content, tags)
        VALUES ('delete', old.fact_id, old.content, old.tags);
    INSERT INTO facts_fts(rowid, content, tags)
        VALUES (new.fact_id, new.content, new.tags);
END;

CREATE TABLE IF NOT EXISTS memory_banks (
    bank_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_name  TEXT NOT NULL UNIQUE,
    vector     BLOB NOT NULL,
    dim        INTEGER NOT NULL,
    fact_count INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Trust adjustment constants
_HELPFUL_DELTA   =  0.05
_UNHELPFUL_DELTA = -0.10
_TRUST_MIN       =  0.0
_TRUST_MAX       =  1.0

# Entity extraction patterns
_RE_CAPITALIZED  = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
_RE_DOUBLE_QUOTE = re.compile(r'"([^"]+)"')
_RE_SINGLE_QUOTE = re.compile(r"'([^']+)'")
_RE_AKA          = re.compile(
    r'(\w+(?:\s+\w+)*)\s+(?:aka|also known as)\s+(\w+(?:\s+\w+)*)',
    re.IGNORECASE,
)


def _clamp_trust(value: float) -> float:
    return max(_TRUST_MIN, min(_TRUST_MAX, value))


# ---------------------------------------------------------------------------
# Controlled entity-type vocabulary (Part 2, extension item 4 / 3b micro-gap)
# ---------------------------------------------------------------------------
#
# The ``entities`` table already carries an ``entity_type`` column that DEFAULTS
# to the free-text sentinel ``'unknown'`` (see _SCHEMA). The only genuine gap for
# the "light user knowledge-graph" was that this column is unenforced, so a
# company / client / project / person could never be typed reliably. This is a
# LIGHT controlled vocabulary on the EXISTING column, not a new graph DB:
#   - typing an entity is OPTIONAL and ADDITIVE — untyped legacy entities keep
#     ``'unknown'`` and every existing code path is unchanged;
#   - when a type IS given it is validated against this vocabulary and coerced to
#     ``'other'`` when it is not recognized (never rejected with a crash);
#   - ``'unknown'`` (the table default, "never classified") and ``'other'``
#     ("classified, but none of the specific types fit") are BOTH valid and are
#     deliberately distinct so a never-typed entity is distinguishable from one a
#     classifier looked at and could not place.
#
# Keep this set TINY and intentional. The user asked for primitive reinforcement,
# not an ontology — do not grow it into a taxonomy and do not train a model.
ENTITY_TYPES: frozenset[str] = frozenset(
    {
        "person",
        "company",
        "client",
        "project",
        "topic",
        "preference",
        "place",
        "other",
        "unknown",
    }
)

# The fallback when a classifier ran but no specific type fit. Distinct from the
# table default ``'unknown'`` (never classified).
ENTITY_TYPE_OTHER = "other"

# The table default — an entity that has never been classified.
ENTITY_TYPE_UNKNOWN = "unknown"


def normalize_entity_type(entity_type: "str | None") -> str:
    """Validate/coerce a free-text type against :data:`ENTITY_TYPES`.

    Returns a member of :data:`ENTITY_TYPES`:
      - ``None`` / empty / whitespace -> ``'unknown'`` (treated as "not given");
      - a recognized type (case-insensitive, surrounding whitespace stripped)
        -> that canonical lowercase type;
      - anything else -> ``'other'`` (coerced, never rejected with an error).

    Pure function, no I/O. This is the single choke-point every typed write goes
    through, so an invalid type can never reach the column.
    """
    if entity_type is None:
        return ENTITY_TYPE_UNKNOWN
    candidate = str(entity_type).strip().lower()
    if not candidate:
        return ENTITY_TYPE_UNKNOWN
    if candidate in ENTITY_TYPES:
        return candidate
    return ENTITY_TYPE_OTHER


# Heuristic classification cues. Deliberately tiny and conservative: a cue only
# fires on a clear, unambiguous surface signal; everything else stays unknown so
# the caller can decide. Order matters — the first matching rule wins. No model,
# no network, no I/O; pure string inspection over the entity name (+ optional
# context text).
_CLASSIFY_SUFFIXES: tuple[tuple[str, str], ...] = (
    # company legal-suffix cues
    (" inc", "company"),
    (" inc.", "company"),
    (" llc", "company"),
    (" ltd", "company"),
    (" ltd.", "company"),
    (" corp", "company"),
    (" corp.", "company"),
    (" gmbh", "company"),
    (" co.", "company"),
    (" plc", "company"),
)
_CLASSIFY_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("client", "client"),
    ("customer", "client"),
    ("project", "project"),
)


def classify_entity_type(name: str, context: "str | None" = None) -> str:
    """Heuristically map an entity to a controlled type, or ``'unknown'``.

    A deliberately conservative, no-model, no-I/O heuristic over the entity
    ``name`` (and optional ``context`` text). It only fires on a clear surface
    cue:
      - a company legal suffix in the name (Inc, LLC, Ltd, Corp, GmbH, ...)
        -> ``'company'``;
      - a ``client``/``customer`` or ``project`` keyword in the name or context
        -> ``'client'`` / ``'project'``.
    When no cue matches it returns ``'unknown'`` (NOT ``'other'``) so the caller
    can decide whether to leave it untyped or pass an explicit type. The result
    is always a member of :data:`ENTITY_TYPES`.

    This is the optional "classify hook": :meth:`MemoryStore.set_entity_type`
    can use it when no explicit type is given, but it never overrides an explicit
    type and never reaches the network.
    """
    hay_name = f" {(name or '').strip().lower()} "
    for suffix, etype in _CLASSIFY_SUFFIXES:
        # Match a legal suffix as a trailing token of the name.
        if hay_name.rstrip().endswith(suffix.rstrip()):
            return etype
    hay = f"{hay_name}{(context or '').strip().lower()}"
    for keyword, etype in _CLASSIFY_KEYWORDS:
        if keyword in hay:
            return etype
    return ENTITY_TYPE_UNKNOWN


# Default namespace for facts written by the orchestrator's own reconcile path
# (MEMORY-POLICY: source_store column). The promotion path writes
# 'orchestrator/shared'; per-agent scopes use 'agent/<slug>'.
_DEFAULT_SOURCE_STORE = "orchestrator/self"

# UUID namespace for deriving stable, reproducible ext_keys from content. A
# fixed namespace makes uuid5(content) deterministic across processes and runs,
# so a legacy-row backfill and a fresh insert of the same content agree.
_EXT_KEY_NAMESPACE = uuid.UUID("6f9b8c4e-3d2a-4b1f-9a7c-2e5d8f1a0b3c")


# Namespaces whose facts are SELF-GENERATED by the orchestrator and therefore
# eligible for a tamper-evident signature. A signature on these rows proves the
# row's content was produced by this install's own reconcile/promotion path and
# has not been mutated since. Cross-fed rows (Honcho/GBrain, agent/<slug>) are
# NEVER signed here, so a forged metadata.source_tier="user_authored" tag on
# such a row cannot acquire a valid signature (web-validation BUILD-QUEUE #2).
_SELF_SOURCE_STORES: frozenset[str] = frozenset(
    {"orchestrator/self", "orchestrator/shared"}
)

# Stable per-install signing key, lazily created under $HERMES_HOME. Reuses the
# dreaming review HMAC key when present so there is one local memory-signing
# secret; otherwise a dedicated 0600 key file is created once. Cached per
# process after first resolution.
_SIGNING_KEY: bytes | None = None
_SIGNING_KEY_LOCK = threading.Lock()
_SIGNING_KEY_FILENAME = "memory_signing_key"
# The dreaming review plugin's per-home HMAC key (review.py::_resolve_hmac_key).
_REVIEW_HMAC_FILENAME = ".review_hmac_key"


def _resolve_signing_key() -> bytes:
    """Return the stable local memory-signing key (32 bytes), creating it once.

    Resolution order (req: reuse the dreaming review HMAC key if present, else
    derive a per-install key from a file under ``$HERMES_HOME``):
      1. ``<HERMES_HOME>/.review_hmac_key`` — the dreaming review plugin's
         existing HMAC secret. Reused when present so there is a SINGLE local
         memory-signing secret across the dreaming + memory provenance planes.
      2. ``<HERMES_HOME>/memory_signing_key`` — a dedicated key created (32
         random bytes, file mode 0600) once and read thereafter.

    NEVER hardcodes a key and NEVER reads a ``HERMES_*`` env var for the secret.
    Cached per process. If the key file cannot be persisted (read-only home),
    an ephemeral in-process key is used so signing/verification stay consistent
    within the run rather than crashing.
    """
    global _SIGNING_KEY
    cached = _SIGNING_KEY
    if cached is not None:
        return cached
    with _SIGNING_KEY_LOCK:
        if _SIGNING_KEY is not None:
            return _SIGNING_KEY
        try:
            from hermes_constants import get_hermes_home
            home = get_hermes_home()
        except Exception:  # pragma: no cover - defensive: home unresolvable
            home = Path.home() / ".hermes"

        # 1. Reuse the dreaming review HMAC key if it already exists.
        review_key_path = home / _REVIEW_HMAC_FILENAME
        if review_key_path.exists():
            try:
                key = bytes.fromhex(
                    review_key_path.read_text(encoding="utf-8").strip()
                )
                if key:
                    _SIGNING_KEY = key
                    return key
            except (ValueError, OSError) as exc:
                logger.warning(
                    "review hmac key unreadable (%s); using memory signing key", exc
                )

        # 2. Dedicated per-install memory-signing key.
        key_path = home / _SIGNING_KEY_FILENAME
        if key_path.exists():
            try:
                key = bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
                if key:
                    _SIGNING_KEY = key
                    return key
            except (ValueError, OSError) as exc:
                logger.warning(
                    "memory signing key unreadable (%s); regenerating", exc
                )

        new_key = os.urandom(32)
        try:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_text(new_key.hex(), encoding="utf-8")
            os.chmod(key_path, 0o600)
        except OSError as exc:
            logger.warning(
                "could not persist memory signing key (%s); using ephemeral key", exc
            )
        _SIGNING_KEY = new_key
        return new_key


def _normalized_content(content: str) -> str:
    """Whitespace-normalized content used for hashing (matches ext_key shape)."""
    return " ".join((content or "").split())


def _compute_content_hash(content: str) -> str:
    """SHA-256 hex of the normalized content (tamper detection over content)."""
    return hashlib.sha256(
        _normalized_content(content).encode("utf-8")
    ).hexdigest()


def _canonical_sig_body(ext_key: str, content_hash: str, source_store: str) -> str:
    """Canonical string signed by the HMAC: ext_key + content_hash + source_store.

    A fixed, unambiguous separator joins the three fields so two different field
    triples can never produce the same canonical string (the separator cannot
    appear in a hex hash or in the controlled key/namespace values).
    """
    return "\x1f".join((ext_key, content_hash, source_store))


def _compute_signature(
    ext_key: str, content_hash: str, source_store: str
) -> str:
    """HMAC-SHA256 over the canonical (ext_key, content_hash, source_store)."""
    body = _canonical_sig_body(ext_key, content_hash, source_store)
    return hmac.new(
        _resolve_signing_key(), body.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _content_ext_key(content: str) -> str:
    """Derive a stable, reproducible external key (UUID) from fact content.

    Content is the dedup key (UNIQUE column), so a content-derived UUID5 is a
    stable external reference that survives ``fact_id`` recycling and is
    reproducible: the same content always maps to the same key. Whitespace is
    normalized so trivial spacing differences do not fork the key.
    """
    normalized = " ".join((content or "").split())
    return str(uuid.uuid5(_EXT_KEY_NAMESPACE, normalized))


def _new_ext_key() -> str:
    """Generate a fresh random external key (for supersede's new fact)."""
    return str(uuid.uuid4())


def _utc_now_iso() -> str:
    """Current UTC timestamp in SQLite-friendly ISO form (matches CURRENT_TIMESTAMP shape)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# NL -> keyword OR-expansion for FTS5 reads.
#
# FTS5 implicitly ANDs the terms in a bare MATCH query, so a natural-language
# query ("what is my dog's name") forces a miss whenever the stored fact lacks
# the filler words. OR-joining the surviving content terms recovers the hit.
# memory-stack/recall_probe.py proved the lift (0.62 NL vs 1.00 OR); this is the
# same deterministic stopword set, kept additive and read-only.
_OR_STOPWORDS = frozenset({
    "what", "is", "my", "do", "i", "the", "a", "an", "in", "of", "to",
    "where", "which", "are", "you", "does", "how", "me", "on",
})

# Split on any run of non-word characters (keeps unicode word chars).
_RE_NONWORD = re.compile(r"\W+", re.UNICODE)


def _or_expand_query(query: str) -> str:
    """Expand a natural-language query into an FTS5 ``term OR term ...`` query.

    Splits on non-word characters, lowercases, drops a small stopword set and
    single-character tokens (apostrophe fragments like the "s" in "dog's", which
    are not useful FTS5 terms), and OR-joins the survivors. If nothing survives
    (e.g. the query is all stopwords or punctuation), the original query is
    returned unchanged so the caller never ends up with an empty MATCH. Pure
    function, no I/O.
    """
    terms = [
        t for t in _RE_NONWORD.split(query.lower())
        if len(t) > 1 and t not in _OR_STOPWORDS
    ]
    return " OR ".join(terms) if terms else query.strip()


class MemoryStore:
    """SQLite-backed fact store with entity resolution and trust scoring."""

    def __init__(
        self,
        db_path: "str | Path | None" = None,
        default_trust: float = 0.5,
        hrr_dim: int = 1024,
    ) -> None:
        if db_path is None:
            from hermes_constants import get_hermes_home
            db_path = str(get_hermes_home() / "memory_store.db")
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_trust = _clamp_trust(default_trust)
        self.hrr_dim = hrr_dim
        self._hrr_available = hrr._HAS_NUMPY
        self._conn: sqlite3.Connection = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10.0,
        )
        self._lock = threading.RLock()
        self._conn.row_factory = sqlite3.Row
        # Lazily-opened, read-only WAL connection used by search_facts_readonly.
        # WAL permits concurrent readers, so pure recalls do not serialize behind
        # the single write connection + RLock. Opened on first read-only use.
        self._read_conn: "sqlite3.Connection | None" = None
        self._read_lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create tables, indexes, and triggers if they do not exist. Enable WAL mode."""
        # Use the shared WAL-fallback helper so memory_store.db degrades
        # gracefully on NFS/SMB/FUSE-mounted HERMES_HOME (same issue as
        # state.db / kanban.db — see hermes_state._WAL_INCOMPAT_MARKERS).
        from hermes_state import apply_wal_with_fallback
        apply_wal_with_fallback(self._conn, db_label="memory_store.db (holographic)")
        self._conn.executescript(_SCHEMA)
        # Migrate: add hrr_vector column if missing (safe for existing databases)
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(facts)").fetchall()}
        if "hrr_vector" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN hrr_vector BLOB")
        self._conn.commit()
        # Sync the external-content FTS5 index BEFORE any UPDATE on facts. A
        # legacy DB whose facts table predates facts_fts has rows that were
        # never indexed; the AFTER UPDATE trigger then issues a 'delete' against
        # an FTS row that does not exist, which SQLite reports as "database disk
        # image is malformed". Rebuilding from the external content makes the
        # index consistent. Cheap + idempotent (a no-op when already in sync),
        # and purely additive: it never touches the facts table rows.
        self._sync_fts_index()
        # Bi-temporal substrate (Decision C). Idempotent + non-destructive.
        self._migrate_bitemporal()
        # Tamper-evident provenance (web-validation BUILD-QUEUE #2). Idempotent
        # + non-destructive. MUST run after _migrate_bitemporal so ext_key /
        # source_store are present for the content_hash backfill.
        self._migrate_provenance()

    def _sync_fts_index(self) -> None:
        """Reconcile the external-content FTS5 index with the ``facts`` table.

        A legacy DB whose ``facts`` rows predate ``facts_fts`` has content-table
        rows that were never written into the FTS index. ``COUNT(*)`` on an
        external-content FTS5 table reflects the content table, so a count
        compare does NOT reveal the desync. We instead run an FTS integrity
        check and rebuild only when it reports damage (the legacy case); a
        healthy in-sync index passes the check and pays nothing but the check.
        The rebuild reads the external content table; it never modifies
        ``facts`` rows, so it is data-safe and idempotent.
        """
        # Skip entirely on an empty table (nothing to index, integrity-check on
        # some builds is noisy on empty external-content tables).
        try:
            has_rows = (
                self._conn.execute("SELECT 1 FROM facts LIMIT 1").fetchone()
                is not None
            )
        except sqlite3.DatabaseError:
            has_rows = True
        if not has_rows:
            return

        needs_rebuild = False
        try:
            self._conn.execute(
                "INSERT INTO facts_fts(facts_fts, rank) VALUES('integrity-check', 1)"
            )
        except sqlite3.DatabaseError:
            # integrity-check raises on a corrupt/desynced external-content
            # index; that is exactly the legacy case we repair.
            needs_rebuild = True

        if needs_rebuild:
            self._conn.execute(
                "INSERT INTO facts_fts(facts_fts) VALUES('rebuild')"
            )
            self._conn.commit()

    def _migrate_bitemporal(self) -> None:
        """Add the bi-temporal / namespace columns to ``facts`` if missing.

        Decision C (PHASE3): makes the holographic fact store bi-temporal so
        supersession invalidates (sets ``t_invalid``) instead of deleting, and
        adds a stable external key (``ext_key``) plus a namespace
        (``source_store``) column for one-plane-per-fact routing and the
        ``orchestrator/shared`` promotion namespace (Decision B).

        Contract (req #2 / #3 data-safety):
          - IDEMPOTENT: a column is added only if ``PRAGMA table_info`` shows it
            missing, so re-running ``_init_db`` on an already-migrated DB is a
            no-op and never duplicates a column.
          - NON-DESTRUCTIVE: every existing row is preserved; columns are only
            ADDed and backfilled, never dropped/deleted. ``ext_key`` is
            backfilled with a deterministic content hash (reproducible across
            runs), ``t_valid`` from ``created_at``, ``source_store`` with the
            default self namespace.
          - Runs against whatever DB path this store was opened on (tests use
            temp DBs); it does nothing special for the live store.
        """
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(facts)").fetchall()
        }

        # SQLite forbids a non-constant DEFAULT (e.g. CURRENT_TIMESTAMP) in
        # ALTER TABLE ADD COLUMN, so we add the column nullable and backfill
        # explicitly. Each ALTER is guarded so the migration is idempotent.
        if "ext_key" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN ext_key TEXT")
        if "t_valid" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN t_valid TIMESTAMP")
        if "t_invalid" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN t_invalid TIMESTAMP")
        if "supersedes_id" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN supersedes_id TEXT")
        if "source_store" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN source_store TEXT")
        self._conn.commit()

        # Backfill any rows missing the new values. This covers both the legacy
        # rows that predate the columns and is harmless on an already-backfilled
        # DB (the WHERE ... IS NULL clauses match nothing). t_valid defaults to
        # created_at; source_store to the self namespace.
        self._conn.execute(
            "UPDATE facts SET t_valid = created_at WHERE t_valid IS NULL"
        )
        self._conn.execute(
            "UPDATE facts SET source_store = ? WHERE source_store IS NULL",
            (_DEFAULT_SOURCE_STORE,),
        )
        # ext_key is a deterministic content hash so it is reproducible: the same
        # content normally backfills to the same key. BUT _content_ext_key
        # NORMALIZES internal whitespace while the facts.content column is UNIQUE
        # WITHOUT normalization, so two distinct stored rows that differ only in
        # whitespace ("the  port is 8000" vs "the port is 8000") derive the SAME
        # ext_key. The UNIQUE index below would then raise IntegrityError and
        # brick the store FOREVER (it can never be opened again).
        #
        # To GUARANTEE uniqueness while preserving determinism when there is no
        # collision, we track the keys already used (including any non-null
        # ext_keys already present on the table) and, on a collision, disambiguate
        # DETERMINISTICALLY by appending "-<fact_id>" (fact_id is the PK, so it is
        # unique per row). Backfill per-row only where missing.
        used_keys: set[str] = {
            row[0]
            for row in self._conn.execute(
                "SELECT ext_key FROM facts WHERE ext_key IS NOT NULL"
            ).fetchall()
            if row[0] is not None
        }
        rows = self._conn.execute(
            "SELECT fact_id, content FROM facts WHERE ext_key IS NULL"
        ).fetchall()
        for row in rows:
            candidate = _content_ext_key(row["content"])
            if candidate in used_keys:
                # Deterministic, per-row-unique disambiguation. fact_id is the PK
                # so "<key>-<fact_id>" can never collide with another row's
                # disambiguated key; the suffixed form is also outside the uuid5
                # space so it cannot collide with a future content-derived key.
                candidate = f"{candidate}-{row['fact_id']}"
            used_keys.add(candidate)
            self._conn.execute(
                "UPDATE facts SET ext_key = ? WHERE fact_id = ?",
                (candidate, row["fact_id"]),
            )
        self._conn.commit()

        # Indexes (idempotent via IF NOT EXISTS). UNIQUE on ext_key enforces the
        # stable-external-key invariant; the others speed namespace and
        # validity-window filtering on the read path.
        self._conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_ext_key
                ON facts(ext_key);
            CREATE INDEX IF NOT EXISTS idx_facts_source_store
                ON facts(source_store);
            CREATE INDEX IF NOT EXISTS idx_facts_t_invalid
                ON facts(t_invalid);
            """
        )
        self._conn.commit()

    def _migrate_provenance(self) -> None:
        """Add the tamper-evident provenance columns to ``facts`` if missing.

        Adds two additive, nullable columns (web-validation BUILD-QUEUE #2):
          - ``content_hash`` TEXT: SHA-256 hex of the normalized content. Lets
            :meth:`verify_fact` detect a post-hoc content mutation.
          - ``signature``    TEXT: HMAC-SHA256 over the canonical
            ``ext_key + content_hash + source_store`` keyed by the local
            memory-signing key. Present ONLY on self-generated rows
            (``source_store`` in :data:`_SELF_SOURCE_STORES`); a cross-fed row
            (Honcho/GBrain, agent/<slug>) is left unsigned, so a forged
            ``source_tier`` tag cannot acquire a valid signature.

        Contract (req #2 / #3 data-safety), identical to ``_migrate_bitemporal``:
          - IDEMPOTENT: a column is added only when ``PRAGMA table_info`` shows
            it missing, so re-running ``_init_db`` is a no-op.
          - NON-DESTRUCTIVE: columns are only ADDed; existing rows are preserved.
            Legacy rows get ``content_hash`` BACKFILLED (so verification can
            detect later tampering) but ``signature`` is left NULL — a legacy
            row is treated as UNSIGNED, never crash, never silently "valid".
        """
        columns = {
            row[1] for row in self._conn.execute("PRAGMA table_info(facts)").fetchall()
        }

        if "content_hash" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN content_hash TEXT")
        if "signature" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN signature TEXT")
        self._conn.commit()

        # Backfill content_hash for any row missing it (legacy rows + rows that
        # predate this migration). signature is deliberately NOT backfilled:
        # legacy rows are unsigned. The WHERE ... IS NULL clause makes this a
        # no-op on an already-backfilled DB.
        rows = self._conn.execute(
            "SELECT fact_id, content FROM facts WHERE content_hash IS NULL"
        ).fetchall()
        for row in rows:
            self._conn.execute(
                "UPDATE facts SET content_hash = ? WHERE fact_id = ?",
                (_compute_content_hash(row["content"]), row["fact_id"]),
            )
        self._conn.commit()

    def _maybe_sign(
        self, ext_key: str, content: str, source_store: str
    ) -> "tuple[str, str | None]":
        """Return ``(content_hash, signature_or_None)`` for a fact being written.

        ``content_hash`` is always computed. ``signature`` is computed ONLY for
        self-generated namespaces (:data:`_SELF_SOURCE_STORES`); for any other
        namespace (cross-fed Honcho/GBrain rows, per-agent scopes) it is None so
        the row stays unsigned and cannot be trusted by a downstream floor gate.
        """
        content_hash = _compute_content_hash(content)
        signature: str | None = None
        if source_store in _SELF_SOURCE_STORES:
            signature = _compute_signature(ext_key, content_hash, source_store)
        return content_hash, signature

    def verify_fact(self, ext_key: str) -> bool:
        """Recompute provenance for ``ext_key`` and report whether it is intact.

        Returns ``True`` ONLY when the row exists, carries a non-null
        ``signature``, and that signature still matches an HMAC recomputed from
        the row's CURRENT content (re-hashed live), its ``ext_key`` and its
        ``source_store``. Therefore:
          - a self-generated, untampered row verifies True;
          - a row whose ``content`` was tampered after signing (so the live hash
            no longer matches the signed hash) verifies False;
          - a legacy / cross-fed UNSIGNED row (``signature`` IS NULL) verifies
            False (it is "not a verified self-fact"), never raising.

        Pure read: no UPDATE/commit. Uses the write connection under the lock so
        it reflects the latest committed state.
        """
        with self._lock:
            row = self._conn.execute(
                """
                SELECT content, source_store, signature
                FROM facts WHERE ext_key = ?
                """,
                (ext_key,),
            ).fetchone()
        if row is None:
            return False
        signature = row["signature"]
        if not signature:
            # Unsigned (legacy or cross-fed): not a verified self-fact.
            return False
        source_store = row["source_store"] or _DEFAULT_SOURCE_STORE
        # Recompute the hash from the CURRENT content so a post-sign content
        # mutation fails verification.
        live_hash = _compute_content_hash(row["content"])
        expected = _compute_signature(ext_key, live_hash, source_store)
        return hmac.compare_digest(signature, expected)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_fact(
        self,
        content: str,
        category: str = "general",
        tags: str = "",
        *,
        source_store: str | None = None,
        defer_enrichment: bool = False,
    ) -> int:
        """Insert a fact and return its fact_id.

        Deduplicates by content (UNIQUE constraint). On duplicate, returns
        the existing fact_id without modifying the row. Extracts entities from
        the content and links them to the fact.

        Bi-temporal / namespace fields (Decision C): every insert sets a stable
        ``ext_key`` (content-hash UUID), ``t_valid`` (now), and ``source_store``
        (the namespace; defaults to ``orchestrator/self``).

        Two-phase write (``defer_enrichment``):
          - ``False`` (default, back-compat): the full path runs as before plus
            entity extraction, HRR encode and the O(n) bank rebuild.
          - ``True``: ONLY the hot INSERT runs (content, category, tags, trust,
            ext_key, t_valid, source_store). Entity extraction, HRR encode and
            bank rebuild are SKIPPED, so the write is cheap and the fact is
            immediately FTS5-recallable via ``search_facts_readonly``. The
            enrichment is left for a later background pass.
        """
        ns = source_store if source_store is not None else _DEFAULT_SOURCE_STORE
        with self._lock:
            content = content.strip()
            if not content:
                raise ValueError("content must not be empty")

            ext_key = _content_ext_key(content)
            now = _utc_now_iso()
            content_hash, signature = self._maybe_sign(ext_key, content, ns)
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO facts
                        (content, category, tags, trust_score,
                         ext_key, t_valid, source_store,
                         content_hash, signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (content, category, tags, self.default_trust,
                     ext_key, now, ns, content_hash, signature),
                )
                self._conn.commit()
                # lastrowid is always an int after a successful INSERT (sqlite3
                # types it Optional; bind to a local so the narrowing sticks).
                last_id = cur.lastrowid
                assert last_id is not None
                fact_id: int = last_id
            except sqlite3.IntegrityError:
                # Two UNIQUE constraints can fire here and they need OPPOSITE
                # handling, so we MUST distinguish them rather than blindly
                # treating every IntegrityError as a content duplicate:
                #   - content UNIQUE  -> the fact already exists: dedup, return
                #     the existing fact_id (the established behavior).
                #   - ext_key UNIQUE  -> the CONTENT is NEW but its content-derived
                #     ext_key collides with an existing row that differs only in
                #     whitespace (_content_ext_key normalizes; content does not).
                #     The old code's content lookup found nothing here and crashed
                #     on int(None["fact_id"]). Instead, re-insert with a FRESH
                #     unique ext_key and CONTINUE the normal path so the new fact
                #     is stored, enriched and returned. The store never bricks.
                existing = self._conn.execute(
                    "SELECT fact_id FROM facts WHERE content = ?", (content,)
                ).fetchone()
                if existing is not None:
                    # Duplicate content — return existing id.
                    return int(existing["fact_id"])
                # ext_key collision on NEW content: retry with a fresh uuid4 key.
                # The signature binds the ext_key, so re-sign against the new key.
                retry_ext_key = _new_ext_key()
                retry_hash, retry_sig = self._maybe_sign(
                    retry_ext_key, content, ns
                )
                cur = self._conn.execute(
                    """
                    INSERT INTO facts
                        (content, category, tags, trust_score,
                         ext_key, t_valid, source_store,
                         content_hash, signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (content, category, tags, self.default_trust,
                     retry_ext_key, now, ns, retry_hash, retry_sig),
                )
                self._conn.commit()
                last_id = cur.lastrowid
                assert last_id is not None
                fact_id = last_id

            if defer_enrichment:
                # Hot write only: skip entity extraction + HRR encode + bank
                # rebuild. The row is already FTS5-indexed by the AFTER INSERT
                # trigger, so it is immediately recallable.
                return fact_id

            # Entity extraction and linking
            for name in self._extract_entities(content):
                entity_id = self._resolve_entity(name)
                self._link_fact_entity(fact_id, entity_id)

            # Compute HRR vector after entity linking
            self._compute_hrr_vector(fact_id, content)
            self._rebuild_bank(category)

            return fact_id

    def invalidate(
        self,
        ext_key: str,
        *,
        t_invalid: "str | float | int | None" = None,
    ) -> bool:
        """Mark a fact invalid (bi-temporal). Never deletes.

        SETS ``t_invalid`` (to ``now`` or the given timestamp) on the row whose
        ``ext_key`` matches AND that is still currently valid (``t_invalid IS
        NULL``). The row, its content and its history are preserved; it simply
        disappears from the default ``search_facts_readonly`` view and reappears
        under an ``as_of`` earlier than ``t_invalid``.

        Returns ``True`` if a row was invalidated, ``False`` if no matching,
        still-valid row existed (idempotent: re-invalidating is a no-op False).
        """
        ts = (
            _utc_now_iso()
            if t_invalid is None
            else self._normalize_as_of(t_invalid)
        )
        with self._lock:
            cur = self._conn.execute(
                """
                UPDATE facts
                SET t_invalid = ?, updated_at = CURRENT_TIMESTAMP
                WHERE ext_key = ? AND t_invalid IS NULL
                """,
                (ts, ext_key),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def supersede(
        self,
        old_ext_key: str,
        new_content: str,
        category: str = "general",
        tags: str = "",
        *,
        source_store: str | None = None,
        defer_enrichment: bool = False,
        t_invalid: "str | float | int | None" = None,
    ) -> str:
        """Replace a fact with a newer one (recency-wins), bi-temporally.

        In ONE transaction: adds the new fact (linking ``supersedes_id =
        old_ext_key``) AND invalidates the old fact (sets ``t_invalid``). The
        old fact is never deleted, so it stays recallable via ``as_of`` before
        its invalidation; the new fact is the one returned by default reads.

        Returns the new fact's ``ext_key``. If the new content duplicates an
        existing fact (content UNIQUE), that existing row is reused/relinked as
        the superseding fact rather than inserting a duplicate.

        ``defer_enrichment`` mirrors :meth:`add_fact`: when ``True`` the new
        fact's entity/HRR/bank enrichment is skipped (hot write only); it is
        still immediately FTS5-recallable.
        """
        ns = source_store if source_store is not None else _DEFAULT_SOURCE_STORE
        new_content = new_content.strip()
        if not new_content:
            raise ValueError("new_content must not be empty")

        new_ext_key = _content_ext_key(new_content)
        now = _utc_now_iso()
        invalid_ts = (
            now if t_invalid is None else self._normalize_as_of(t_invalid)
        )

        with self._lock:
            # Insert (or locate) the new fact and invalidate the old one as one
            # atomic unit so a crash cannot leave the old fact invalidated with
            # no replacement, or the replacement with the old still valid.
            content_hash, signature = self._maybe_sign(new_ext_key, new_content, ns)
            try:
                cur = self._conn.execute(
                    """
                    INSERT INTO facts
                        (content, category, tags, trust_score,
                         ext_key, t_valid, source_store, supersedes_id,
                         content_hash, signature)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_content, category, tags, self.default_trust,
                     new_ext_key, now, ns, old_ext_key, content_hash, signature),
                )
                last_id = cur.lastrowid
                assert last_id is not None
                new_fact_id: int = last_id
                inserted = True
            except sqlite3.IntegrityError:
                # Same two-UNIQUE-constraint ambiguity as add_fact: distinguish
                # a content duplicate from an ext_key collision on NEW content.
                row = self._conn.execute(
                    "SELECT fact_id, ext_key FROM facts WHERE content = ?",
                    (new_content,),
                ).fetchone()
                if row is not None:
                    # New content already exists. Reuse that row as the
                    # superseding fact and stamp the supersedes link / namespace.
                    new_fact_id = int(row["fact_id"])
                    new_ext_key = str(row["ext_key"])
                    # The namespace is (re)stamped here, and the signature binds
                    # ext_key + content_hash + source_store, so recompute both
                    # against the reused row's existing ext_key and new namespace.
                    reuse_hash, reuse_sig = self._maybe_sign(
                        new_ext_key, new_content, ns
                    )
                    self._conn.execute(
                        """
                        UPDATE facts
                        SET supersedes_id = ?, source_store = ?,
                            content_hash = ?, signature = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE fact_id = ?
                        """,
                        (old_ext_key, ns, reuse_hash, reuse_sig, new_fact_id),
                    )
                    inserted = False
                else:
                    # ext_key collision on NEW content (a whitespace-variant of an
                    # existing fact's content). Re-insert with a FRESH unique
                    # ext_key so the new fact is stored rather than bricking; it is
                    # still a genuine insert, so it follows the inserted=True path
                    # (entity/HRR enrichment below).
                    new_ext_key = _new_ext_key()
                    retry_hash, retry_sig = self._maybe_sign(
                        new_ext_key, new_content, ns
                    )
                    cur = self._conn.execute(
                        """
                        INSERT INTO facts
                            (content, category, tags, trust_score,
                             ext_key, t_valid, source_store, supersedes_id,
                             content_hash, signature)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (new_content, category, tags, self.default_trust,
                         new_ext_key, now, ns, old_ext_key,
                         retry_hash, retry_sig),
                    )
                    last_id = cur.lastrowid
                    assert last_id is not None
                    new_fact_id = last_id
                    inserted = True

            # Invalidate the old fact (only if still valid) in the same txn.
            self._conn.execute(
                """
                UPDATE facts
                SET t_invalid = ?, updated_at = CURRENT_TIMESTAMP
                WHERE ext_key = ? AND t_invalid IS NULL
                """,
                (invalid_ts, old_ext_key),
            )
            self._conn.commit()

            # Enrichment (skipped for hot writes, and only for a fresh insert).
            if inserted and not defer_enrichment:
                for name in self._extract_entities(new_content):
                    entity_id = self._resolve_entity(name)
                    self._link_fact_entity(new_fact_id, entity_id)
                self._compute_hrr_vector(new_fact_id, new_content)
                self._rebuild_bank(category)

            return new_ext_key

    def search_facts(
        self,
        query: str,
        category: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
    ) -> list[dict]:
        """Full-text search over facts using FTS5.

        Returns a list of fact dicts ordered by FTS5 rank, then trust_score
        descending. Also increments retrieval_count for matched facts.
        """
        with self._lock:
            query = query.strip()
            if not query:
                return []

            params: list = [query, min_trust]
            category_clause = ""
            if category is not None:
                category_clause = "AND f.category = ?"
                params.append(category)
            params.append(limit)

            sql = f"""
                SELECT f.fact_id, f.content, f.category, f.tags,
                       f.trust_score, f.retrieval_count, f.helpful_count,
                       f.created_at, f.updated_at
                FROM facts f
                JOIN facts_fts fts ON fts.rowid = f.fact_id
                WHERE facts_fts MATCH ?
                  AND f.trust_score >= ?
                  {category_clause}
                ORDER BY fts.rank, f.trust_score DESC
                LIMIT ?
            """

            rows = self._conn.execute(sql, params).fetchall()
            results = [self._row_to_dict(r) for r in rows]

            if results:
                ids = [r["fact_id"] for r in results]
                placeholders = ",".join("?" * len(ids))
                self._conn.execute(
                    f"UPDATE facts SET retrieval_count = retrieval_count + 1 WHERE fact_id IN ({placeholders})",
                    ids,
                )
                self._conn.commit()

            return results

    def _get_read_conn(self) -> sqlite3.Connection:
        """Return a cached read-only WAL connection, opening it on first use.

        The connection is opened with ``mode=ro`` (URI) so it can never write,
        and WAL lets it read concurrently with the single write connection.
        Falls back to the existing write connection only if the read-only open
        fails (e.g. a non-URI-capable build), so the read path always works.
        """
        conn = self._read_conn
        if conn is not None:
            return conn
        with self._read_lock:
            if self._read_conn is None:
                try:
                    ro = sqlite3.connect(
                        f"file:{self.db_path}?mode=ro",
                        uri=True,
                        check_same_thread=False,
                        timeout=10.0,
                    )
                    ro.row_factory = sqlite3.Row
                    self._read_conn = ro
                except sqlite3.OperationalError:
                    # Read-only open unavailable; reuse the write connection.
                    # search_facts_readonly stays a pure read regardless because
                    # it never issues UPDATE/commit.
                    self._read_conn = self._conn
            return self._read_conn

    def search_facts_readonly(
        self,
        query: str,
        category: str | None = None,
        min_trust: float = 0.3,
        limit: int = 10,
        or_expand: bool = False,
        *,
        as_of: "str | float | int | None" = None,
        source_store: str | None = None,
    ) -> list[dict]:
        """Pure read-only full-text search over facts (no write on read).

        Mirrors ``search_facts``'s FTS5 SELECT exactly, but:
          - does NOT issue the ``retrieval_count`` UPDATE+commit (pure read);
          - does NOT take the write RLock or a write transaction;
          - runs on a SEPARATE read-only WAL connection, so concurrent recalls
            do not serialize behind the single write connection + RLock.

        ``query`` may already be OR-expanded by the caller; alternatively set
        ``or_expand=True`` to expand a natural-language query internally via
        :func:`_or_expand_query` before the MATCH (the 0.62 -> 1.00 recall fix).

        Bi-temporal / namespace filtering (Decision C):
          - By DEFAULT only currently-valid facts are returned (``t_invalid IS
            NULL``). An invalidated/superseded fact disappears from the default
            view without being deleted.
          - ``as_of`` (ISO timestamp string or epoch seconds) switches to "as
            of that instant" reasoning: a fact is included when it was already
            valid (``t_valid <= as_of``) and not yet invalidated at that instant
            (``t_invalid IS NULL OR t_invalid > as_of``). This makes a
            superseded fact recallable as of a time before its invalidation.
          - ``source_store`` restricts to a single namespace
            (``orchestrator/self``, ``orchestrator/shared``, ``agent/<slug>``);
            ``None`` (default) is namespace-agnostic for back-compat.

        Back-compat: ``search_facts`` is unchanged and still writes
        ``retrieval_count``. ``as_of`` / ``source_store`` default to ``None``,
        preserving the prior call surface.
        """
        query = query.strip()
        if not query:
            return []
        if or_expand:
            query = _or_expand_query(query)

        params: list = [query, min_trust]
        category_clause = ""
        if category is not None:
            category_clause = "AND f.category = ?"
            params.append(category)

        # Bi-temporal validity window. Default: only currently-valid facts.
        if as_of is None:
            validity_clause = "AND f.t_invalid IS NULL"
        else:
            as_of_ts = self._normalize_as_of(as_of)
            validity_clause = (
                "AND f.t_valid <= ? "
                "AND (f.t_invalid IS NULL OR f.t_invalid > ?)"
            )
            params.append(as_of_ts)
            params.append(as_of_ts)

        source_clause = ""
        if source_store is not None:
            source_clause = "AND f.source_store = ?"
            params.append(source_store)

        params.append(limit)

        sql = f"""
            SELECT f.fact_id, f.content, f.category, f.tags,
                   f.trust_score, f.retrieval_count, f.helpful_count,
                   f.created_at, f.updated_at,
                   f.ext_key, f.t_valid, f.t_invalid,
                   f.supersedes_id, f.source_store,
                   f.content_hash, f.signature
            FROM facts f
            JOIN facts_fts fts ON fts.rowid = f.fact_id
            WHERE facts_fts MATCH ?
              AND f.trust_score >= ?
              {category_clause}
              {validity_clause}
              {source_clause}
            ORDER BY fts.rank, f.trust_score DESC
            LIMIT ?
        """

        conn = self._get_read_conn()
        rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    @staticmethod
    def _normalize_as_of(as_of: "str | float | int") -> str:
        """Normalize an ``as_of`` value to the stored TIMESTAMP text shape.

        Accepts an ISO/SQLite timestamp string (passed through) or an epoch
        seconds number (converted to UTC ``YYYY-MM-DD HH:MM:SS``). Strings are
        trusted as-is so callers can pass the exact stored format; this keeps
        the comparison a plain lexicographic TIMESTAMP compare in SQLite.
        """
        if isinstance(as_of, (int, float)):
            return datetime.fromtimestamp(as_of, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        return str(as_of)

    def update_fact(
        self,
        fact_id: int,
        content: str | None = None,
        trust_delta: float | None = None,
        tags: str | None = None,
        category: str | None = None,
    ) -> bool:
        """Partially update a fact. Trust is clamped to [0, 1].

        Returns True if the row existed, False otherwise.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, trust_score FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                return False

            assignments: list[str] = ["updated_at = CURRENT_TIMESTAMP"]
            params: list = []

            if content is not None:
                assignments.append("content = ?")
                params.append(content.strip())
            if tags is not None:
                assignments.append("tags = ?")
                params.append(tags)
            if category is not None:
                assignments.append("category = ?")
                params.append(category)
            if trust_delta is not None:
                new_trust = _clamp_trust(row["trust_score"] + trust_delta)
                assignments.append("trust_score = ?")
                params.append(new_trust)

            params.append(fact_id)
            self._conn.execute(
                f"UPDATE facts SET {', '.join(assignments)} WHERE fact_id = ?",
                params,
            )
            self._conn.commit()

            # If content changed, re-extract entities
            if content is not None:
                self._conn.execute(
                    "DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,)
                )
                for name in self._extract_entities(content):
                    entity_id = self._resolve_entity(name)
                    self._link_fact_entity(fact_id, entity_id)
                self._conn.commit()

            # Recompute HRR vector if content changed
            if content is not None:
                self._compute_hrr_vector(fact_id, content)
            # Rebuild bank for relevant category
            cat = category or self._conn.execute(
                "SELECT category FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()["category"]
            self._rebuild_bank(cat)

            return True

    def remove_fact(self, fact_id: int) -> bool:
        """Delete a fact and its entity links. Returns True if the row existed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, category FROM facts WHERE fact_id = ?", (fact_id,)
            ).fetchone()
            if row is None:
                return False

            self._conn.execute(
                "DELETE FROM fact_entities WHERE fact_id = ?", (fact_id,)
            )
            self._conn.execute("DELETE FROM facts WHERE fact_id = ?", (fact_id,))
            self._conn.commit()
            self._rebuild_bank(row["category"])
            return True

    def list_facts(
        self,
        category: str | None = None,
        min_trust: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """Browse facts ordered by trust_score descending.

        Optionally filter by category and minimum trust score.
        """
        with self._lock:
            params: list = [min_trust]
            category_clause = ""
            if category is not None:
                category_clause = "AND category = ?"
                params.append(category)
            params.append(limit)

            sql = f"""
                SELECT fact_id, content, category, tags, trust_score,
                       retrieval_count, helpful_count, created_at, updated_at
                FROM facts
                WHERE trust_score >= ?
                  {category_clause}
                ORDER BY trust_score DESC
                LIMIT ?
            """
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def record_feedback(self, fact_id: int, helpful: bool) -> dict:
        """Record user feedback and adjust trust asymmetrically.

        helpful=True  -> trust += 0.05, helpful_count += 1
        helpful=False -> trust -= 0.10

        Returns a dict with fact_id, old_trust, new_trust, helpful_count.
        Raises KeyError if fact_id does not exist.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT fact_id, trust_score, helpful_count FROM facts WHERE fact_id = ?",
                (fact_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"fact_id {fact_id} not found")

            old_trust: float = row["trust_score"]
            delta = _HELPFUL_DELTA if helpful else _UNHELPFUL_DELTA
            new_trust = _clamp_trust(old_trust + delta)

            helpful_increment = 1 if helpful else 0
            self._conn.execute(
                """
                UPDATE facts
                SET trust_score    = ?,
                    helpful_count  = helpful_count + ?,
                    updated_at     = CURRENT_TIMESTAMP
                WHERE fact_id = ?
                """,
                (new_trust, helpful_increment, fact_id),
            )
            self._conn.commit()

            return {
                "fact_id":      fact_id,
                "old_trust":    old_trust,
                "new_trust":    new_trust,
                "helpful_count": row["helpful_count"] + helpful_increment,
            }

    # ------------------------------------------------------------------
    # Entity helpers
    # ------------------------------------------------------------------

    def _extract_entities(self, text: str) -> list[str]:
        """Extract entity candidates from text using simple regex rules.

        Rules applied (in order):
        1. Capitalized multi-word phrases  e.g. "John Doe"
        2. Double-quoted terms             e.g. "Python"
        3. Single-quoted terms             e.g. 'pytest'
        4. AKA patterns                    e.g. "Guido aka BDFL" -> two entities

        Returns a deduplicated list preserving first-seen order.
        """
        seen: set[str] = set()
        candidates: list[str] = []

        def _add(name: str) -> None:
            stripped = name.strip()
            if stripped and stripped.lower() not in seen:
                seen.add(stripped.lower())
                candidates.append(stripped)

        for m in _RE_CAPITALIZED.finditer(text):
            _add(m.group(1))

        for m in _RE_DOUBLE_QUOTE.finditer(text):
            _add(m.group(1))

        for m in _RE_SINGLE_QUOTE.finditer(text):
            _add(m.group(1))

        for m in _RE_AKA.finditer(text):
            _add(m.group(1))
            _add(m.group(2))

        return candidates

    def _resolve_entity(self, name: str) -> int:
        """Find an existing entity by name or alias (case-insensitive) or create one.

        Returns the entity_id.
        """
        # Exact name match
        row = self._conn.execute(
            "SELECT entity_id FROM entities WHERE name LIKE ?", (name,)
        ).fetchone()
        if row is not None:
            return int(row["entity_id"])

        # Search aliases — aliases stored as comma-separated; use LIKE with % boundaries
        alias_row = self._conn.execute(
            """
            SELECT entity_id FROM entities
            WHERE ',' || aliases || ',' LIKE '%,' || ? || ',%'
            """,
            (name,),
        ).fetchone()
        if alias_row is not None:
            return int(alias_row["entity_id"])

        # Create new entity
        cur = self._conn.execute(
            "INSERT INTO entities (name) VALUES (?)", (name,)
        )
        self._conn.commit()
        last_id = cur.lastrowid
        assert last_id is not None
        return int(last_id)

    def _link_fact_entity(self, fact_id: int, entity_id: int) -> None:
        """Insert into fact_entities, silently ignore if the link already exists."""
        self._conn.execute(
            """
            INSERT OR IGNORE INTO fact_entities (fact_id, entity_id)
            VALUES (?, ?)
            """,
            (fact_id, entity_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Typed entities — light controlled-vocabulary layer on the EXISTING
    # entities.entity_type column (Part 2, extension item 4 / 3b micro-gap).
    # Additive and reversible: untyped entities keep 'unknown', the free-text
    # column shape is unchanged, and nothing here is on the hot recall path.
    # ------------------------------------------------------------------

    def set_entity_type(
        self,
        name: str,
        entity_type: "str | None" = None,
        *,
        classify: bool = False,
        context: "str | None" = None,
    ) -> str:
        """Assign a controlled ``entity_type`` to an entity (creating it if new).

        Resolves ``name`` to an entity (reusing :meth:`_resolve_entity`, so an
        existing entity or alias is matched and a new one is created when
        absent), then stamps a type drawn from :data:`ENTITY_TYPES`:

          - an explicit ``entity_type`` is validated/coerced via
            :func:`normalize_entity_type` (an unrecognized type becomes
            ``'other'``, never an error);
          - if no explicit type is given and ``classify=True``, the heuristic
            :func:`classify_entity_type` runs over the name (+ ``context``);
          - otherwise the type defaults to ``'unknown'`` (the table default).

        Returns the canonical type that was written. Purely additive: it only
        UPDATEs the ``entity_type`` column, never the entity's facts, links, or
        vectors, so legacy untyped behavior elsewhere is unaffected.
        """
        if entity_type is not None:
            resolved_type = normalize_entity_type(entity_type)
        elif classify:
            resolved_type = classify_entity_type(name, context)
        else:
            resolved_type = ENTITY_TYPE_UNKNOWN
        with self._lock:
            entity_id = self._resolve_entity(name)
            self._conn.execute(
                "UPDATE entities SET entity_type = ? WHERE entity_id = ?",
                (resolved_type, entity_id),
            )
            self._conn.commit()
        return resolved_type

    def get_entity_type(self, name: str) -> "str | None":
        """Return the stored ``entity_type`` for an entity, or ``None`` if absent.

        Matches by exact name OR alias (case-insensitive), mirroring
        :meth:`_resolve_entity`'s lookup, but NEVER creates a row — a pure read.
        Returns the controlled type (``'unknown'`` for a never-typed entity that
        exists) or ``None`` when no such entity is stored.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT entity_type FROM entities WHERE name LIKE ?", (name,)
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    """
                    SELECT entity_type FROM entities
                    WHERE ',' || aliases || ',' LIKE '%,' || ? || ',%'
                    """,
                    (name,),
                ).fetchone()
        if row is None:
            return None
        return row["entity_type"]

    def entities_by_type(self, entity_type: str, limit: int = 100) -> list[dict]:
        """List entities of a given controlled ``entity_type`` (pure read).

        ``entity_type`` is validated/coerced via :func:`normalize_entity_type`
        first, so a query for an unknown type folds into ``'other'`` exactly as
        a write would (a symmetric round-trip). Returns ``{entity_id, name,
        entity_type, aliases}`` dicts ordered by name. Never mutates.
        """
        wanted = normalize_entity_type(entity_type)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT entity_id, name, entity_type, aliases
                FROM entities
                WHERE entity_type = ?
                ORDER BY name
                LIMIT ?
                """,
                (wanted, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def _compute_hrr_vector(self, fact_id: int, content: str) -> None:
        """Compute and store HRR vector for a fact. No-op if numpy unavailable."""
        with self._lock:
            if not self._hrr_available:
                return

            # Get entities linked to this fact
            rows = self._conn.execute(
                """
                SELECT e.name FROM entities e
                JOIN fact_entities fe ON fe.entity_id = e.entity_id
                WHERE fe.fact_id = ?
                """,
                (fact_id,),
            ).fetchall()
            entities = [row["name"] for row in rows]

            vector = hrr.encode_fact(content, entities, self.hrr_dim)
            self._conn.execute(
                "UPDATE facts SET hrr_vector = ? WHERE fact_id = ?",
                (hrr.phases_to_bytes(vector), fact_id),
            )
            self._conn.commit()

    def _rebuild_bank(self, category: str) -> None:
        """Full rebuild of a category's memory bank from all its fact vectors."""
        with self._lock:
            if not self._hrr_available:
                return

            bank_name = f"cat:{category}"
            rows = self._conn.execute(
                "SELECT hrr_vector FROM facts WHERE category = ? AND hrr_vector IS NOT NULL",
                (category,),
            ).fetchall()

            if not rows:
                self._conn.execute("DELETE FROM memory_banks WHERE bank_name = ?", (bank_name,))
                self._conn.commit()
                return

            vectors = [hrr.bytes_to_phases(row["hrr_vector"]) for row in rows]
            bank_vector = hrr.bundle(*vectors)
            fact_count = len(vectors)

            # Check SNR
            hrr.snr_estimate(self.hrr_dim, fact_count)

            self._conn.execute(
                """
                INSERT INTO memory_banks (bank_name, vector, dim, fact_count, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(bank_name) DO UPDATE SET
                    vector = excluded.vector,
                    dim = excluded.dim,
                    fact_count = excluded.fact_count,
                    updated_at = excluded.updated_at
                """,
                (bank_name, hrr.phases_to_bytes(bank_vector), self.hrr_dim, fact_count),
            )
            self._conn.commit()

    def rebuild_all_vectors(self, dim: int | None = None) -> int:
        """Recompute all HRR vectors + banks from text. For recovery/migration.

        Returns the number of facts processed.
        """
        with self._lock:
            if not self._hrr_available:
                return 0

            if dim is not None:
                self.hrr_dim = dim

            rows = self._conn.execute(
                "SELECT fact_id, content, category FROM facts"
            ).fetchall()

            categories: set[str] = set()
            for row in rows:
                self._compute_hrr_vector(row["fact_id"], row["content"])
                categories.add(row["category"])

            for category in categories:
                self._rebuild_bank(category)

            return len(rows)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a sqlite3.Row to a plain dict."""
        return dict(row)

    def close(self) -> None:
        """Close the database connection(s)."""
        # Close the read-only connection first, unless it aliases the write
        # connection (the fallback case), to avoid a double close.
        read_conn = self._read_conn
        if read_conn is not None and read_conn is not self._conn:
            read_conn.close()
        self._read_conn = None
        self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
