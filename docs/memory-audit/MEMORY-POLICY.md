# Memory Policy (req #8): schema, what-to-store, and redaction

Status: LOCKED policy for the OpenComputer v2 memory subsystem. This is the contract the write
path (the reconcile engine and the inline `memory` / `fact_store` tools) enforces. No em dashes.

The guiding rule: memory is curated, not a dump. A fact earns a place in the store only if it is
durable, reusable across sessions, and safe to keep. Everything else stays in the session
transcript (which is itself FTS5-searchable) and is never promoted to the fact store.

---

## 1. The fact schema

The durable fact store is the holographic SQLite store (`plugins/memory/holographic/store.py`).
The `facts` table is extended (Decision C, backup-gated migration) to this shape:

| Column | Type | Meaning |
|--------|------|---------|
| `fact_id` | INTEGER PK | internal autoincrement (recycled, never used as an external reference) |
| `ext_key` | TEXT UNIQUE | stable external key = content-hash UUID; UPDATE/DELETE/invalidate target this, never `fact_id` |
| `content` | TEXT UNIQUE | the fact text (dedup key) |
| `category` | TEXT | semantic bucket (user_pref, project, decision, entity, identity, ...) |
| `tags` | TEXT | comma list, FTS5-indexed |
| `source_store` | TEXT | namespace: `orchestrator/self`, `orchestrator/shared`, `agent/<slug>` |
| `trust_score` | REAL | 0..1, default 0.5, tuned by `fact_feedback` |
| `t_valid` | TIMESTAMP | when the fact became true (defaults to created_at) |
| `t_invalid` | TIMESTAMP NULL | when superseded/invalidated; NULL = currently valid |
| `supersedes_id` | TEXT NULL | the `ext_key` of the fact this one replaced |
| `created_at` / `updated_at` | TIMESTAMP | bookkeeping |
| `hrr_vector` | BLOB | optional HRR vector (numpy) for semantic dedup/probe |

Reads default to `WHERE t_invalid IS NULL` (only currently-valid facts). An `as_of=<ts>` argument
enables "as of" reasoning over the bi-temporal history. Supersession SETS `t_invalid` and links
`supersedes_id`; it never deletes. The only hard DELETE is redaction/GC, which is backup-gated
and pauses for explicit user go (req #2 / #3).

### One plane per fact (the anti-drift rule)

Each fact is written to exactly ONE plane by type, so the same fact never lives in two stores
and drifts:

| Fact type | Plane | Why |
|-----------|-------|-----|
| identity / representation / preferences / standing instructions | Honcho (raw turns; the Deriver synthesizes) | Honcho is the reasoning/identity layer, not a fact KB |
| entities + typed relations | GBrain (queued if its server is down; never dumped into FTS5) | GBrain is the knowledge graph |
| exact / verbatim facts (paths, IDs, decisions, config values, dates) | holographic FTS5 (out-of-band store handle) | needs verbatim, immediately-recallable keyword recall |
| conversation history | session `state.db` FTS5 (already trigger-synced) | the raw transcript, searchable, not curated memory |

---

## 2. What to store vs what NOT to store

STORE (durable, reusable, safe):
- Stable user preferences and standing instructions ("prefers dark mode", "always use TDD").
- Project facts and decisions ("the v2 agent is the hermes-agent fork", "merge layer ships behind
  a flag").
- Entities and their relations (people, repos, services, tickets) and exact identifiers.
- Outcomes worth carrying forward (what worked, what failed and why).

DO NOT STORE:
- Secrets, credentials, API keys, tokens, passwords, private keys, connection strings. (Redacted
  on ingest; see section 3. If a secret somehow lands, it is GC-redactable.)
- Sensitive personal data not needed for the task (government IDs, full payment-card numbers,
  health data, precise home address) unless the user explicitly asked it be remembered, and even
  then minimized.
- Transient state (current mood, "the file I just opened", a one-off scratch value). Lives in the
  session transcript, not the fact store.
- Raw untrusted web/scraped/tool content as if it were a trusted fact. It may be stored ONLY
  wrapped and scanned (section 3 + req #11), never as authoritative memory.
- Large blobs / file contents. Store an artifact reference, not the bytes.
- Low-signal chatter, acknowledgements, and duplicates (the reconcile engine NOOPs these).

Borderline cases resolve to NOT-store by default; the model can always re-derive from the
transcript, which is searchable.

---

## 3. Redaction on ingest (runs BEFORE every fact INSERT)

Every candidate fact passes through a redaction pass before it is written, in the reconcile
engine and the inline `memory` / `fact_store` tools. The patterns below are redacted to a
typed placeholder (e.g. `[REDACTED:aws_key]`) so the fact stays useful without leaking the
secret. This reuses and extends the repo's existing secret-detection surface where possible
(`tools/threat_patterns.py`, the gate audit log redaction) rather than a fresh regex zoo.

Redact (replace with placeholder):
- API keys / tokens: `sk-...`, `sk-or-...`, `ghp_...`, `gho_...`, AWS `AKIA[0-9A-Z]{16}`,
  `xoxb-`/`xoxp-` Slack, bearer tokens, JWTs (`eyJ...\.eyJ...\.`).
- Private keys: `-----BEGIN ... PRIVATE KEY-----` blocks.
- Connection strings with embedded credentials: `scheme://user:pass@host`.
- Passwords in `key=value` / `password: ...` shapes.
- High-confidence PII: full payment-card numbers (Luhn-checked), government IDs in known formats.

Do NOT over-redact: a bare email or a first name is allowed (often legitimately part of identity
memory); redaction targets credentials and high-sensitivity PII, not all personal mentions.

Order of operations on the write path:
1. wrap the candidate as untrusted, run `scan_for_threats(scope="strict")` (prompt-injection);
   a hit means the candidate is NOT stored as a trusted fact (req #11).
2. run the redaction pass (this section); replace secrets with placeholders.
3. dedup (content UNIQUE + HRR-cosine semantic near-dup), emit ADD/UPDATE/DELETE/NOOP.
4. write to the one plane for its type; set bi-temporal fields; never delete on supersede.

On the READ path, every recalled candidate (from any plane) is sanitized + scanned per plane in
its adapter before fusion, so stored content can never be executed as instructions when surfaced
(req #11, the other direction).

---

## 4. Enforcement and tests

- The reconcile engine (Decision C) calls the redaction pass on every candidate; a unit test
  feeds known secrets (a fake `sk-...`, a private-key block, a Luhn-valid card) and asserts the
  stored `content` contains the placeholder, not the secret.
- The inline `memory` / `fact_store` tool path routes through the same redaction, so a user
  pasting a secret and saying "remember this" does not persist the raw secret.
- A store/not-store unit test asserts transient and low-signal candidates NOOP, and durable
  candidates ADD.
- These join the Phase 5 eval + proof script so the policy is provable, not aspirational.
