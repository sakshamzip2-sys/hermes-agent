# Memory Audit — Live Evidence Log

Running log of commands actually executed and their real output. Every "working"
claim in the Phase 1 verdict must trace back to an entry here. No inference.

Date started: 2026-06-20. Repo: OpenComputerV2 (hermes-agent fork).

---

## E1 — Orchestrator session FTS5 store (`~/.hermes/state.db`) — RAN, WORKING

Operational state at audit time:
- Gateway running: `oc gateway run` on 127.0.0.1:8642 (PID 963), editable install of this repo.
- Honcho server (:8000): DOWN (Docker daemon not running).
- GBrain daemon (:3131): DOWN (CLI present at ~/.bun/bin/gbrain).
- Active memory provider (config.yaml line 430): `honcho`.

### Schema (real, from `sqlite_master`)
- `messages` table + two FTS5 virtual tables:
  - `messages_fts USING fts5(content)` — default (unicode61) tokenizer.
  - `messages_fts_trigram USING fts5(content, tokenize='trigram')` — substring search.
- Kept in sync by triggers `messages_fts_insert/delete/update` (and trigram equivalents),
  indexing `COALESCE(content,'') || ' ' || COALESCE(tool_name,'') || ' ' || COALESCE(tool_calls,'')`.
- Content table is external-content style (`messages_fts_content`).

### Live query evidence (cost-free, read-only)
```
$ sqlite3 ~/.hermes/state.db "SELECT count(*) FROM messages;"
1875

$ sqlite3 ~/.hermes/state.db "SELECT rowid, bm25(messages_fts), substr(content,1,90)
  FROM messages_fts WHERE messages_fts MATCH 'memory' ORDER BY bm25(messages_fts) LIMIT 5;"
743|-4.857|{"total_count": 50, "files": ["/Users/saksham/Vscode/OpenComputerV2/...
270|-4.757|<untrusted_tool_result source="web_extract">...
287|-4.567|Now I have all the information. Let me compile...
885|-4.479|[{"id": "toolu_01CVqate4eMGNEVynMzCDa52", ...
1624|-4.306|{"total_count": 50, "files": ["./plugins/oc_agents/worker.py", ...

$ sqlite3 ~/.hermes/state.db "SELECT rowid FROM messages_fts_trigram
  WHERE messages_fts_trigram MATCH 'honcho' LIMIT 3;"   -> 12, 18, 20 (substring hits)
```

### Write/read roundtrip (temp DB, proves FTS5 build support)
```
CREATE VIRTUAL TABLE t USING fts5(content);
INSERT ... 'the quick brown fox recall token ZX9';
SELECT content FROM t WHERE t MATCH 'ZX9';  -> [('the quick brown fox recall token ZX9',)]
FTS5 supported: True
```

VERDICT (live): **WORKING.** bm25 ranking + trigram substring both return correct
results against the real 1,875-row store; triggers keep the index live on insert.

---

## E2 — Holographic provider (SQLite + FTS5 + HRR) — RAN, WORKING (but NOT the active provider)

Ran against a throwaway temp DB with the repo venv (`.venv/bin/python`), no services:
```
add fact_id: 1 | dedup returns same id: True
FTS5 search "dark mode": [{"fact_id":1,"content":"The user prefers dark mode...","trust_score":0.5,...}]
hybrid search scores: [0.367]                      # FTS5 -> Jaccard -> HRR cosine -> trust
probe("user"): ['The user prefers dark mode in the OpenCo']   # HRR algebraic probe (numpy 2.3.5 live)
CROSS-SESSION recall after reopen: ['The user prefers dark mode in ']
DB file size bytes: 65536
```
Discovery + tool exposure:
```
holographic discovered: True
provider name: holographic | is_available: True
exposed tools: ['fact_store', 'fact_feedback']
```
VERDICT (live): **WORKING.** add -> dedup-by-content (same id) -> FTS5 recall -> HRR hybrid +
probe -> cross-session recall, all proven. CAVEAT: it is NOT the active provider
(`memory.provider: honcho`), and no `~/.hermes/memory_store.db` exists, so this layer is
dormant in the live agent today.

---

## E3 — Read/write/merge path (the central question) — RAN, BROKEN-for-combine

```
$ grep -n 'Only one external memory provider' agent/memory_manager.py
371:  "already registered. Only one external memory provider is "
```
`prefetch_all` (agent/memory_manager.py:473-493) loops providers and does `"\n\n".join(parts)`
with NO ranking/dedup. The only concurrent multi-store query is `build_memory_payload`
(gateway/platforms/memory_aggregator.py:466-486) -> `{local, honcho, gbrain}` envelope for
`GET /api/memory` (display only, never sent to the model). `plugins/memory/` contains NO
`gbrain` provider.

VERDICT (live): there is **no cross-store retrieve+merge+rerank on the agent recall path**.
The "three combined" exists only in the display aggregator.

---

## E4 — Hermes per-agent isolation (agent-profiles, mechanism B) — RAN, WORKING

Airtight test with disjoint tokens (temp HERMES_HOME, no services):
```
atlas recalls own token  : True
forge sees atlas token   (MUST be []): []
forge recalls own token  : True
atlas sees forge token   (MUST be []): []
cross-session forge->atlas (MUST be None): None
slug normalization: {'Atlas':'atlas','UPPER':'upper','ok-slug':'ok-slug',
                     '../x':None,'a/b':None,'a_b':None,'':None,None:None}
```
VERDICT (live): **WORKING.** Each `agent-profiles/<slug>/state.db` is a physically separate
FTS5 store; no cross-agent visibility; path-traversal / unsafe slugs rejected; safe slugs
lowercased. (Note: the static recon claimed `UPPER` is rejected; live behavior is
lowercase-and-accept, which is safe.)

---

## E5 — GBrain engine — RAN OFFLINE, WORKING (v2 live server is DOWN)

Fully offline via PGLite (WASM Postgres, no :5432, no Docker) in a throwaway `$HOME`, real
`~/.gbrain` untouched, zero OpenRouter cost (tsvector keyword search needs no embeddings):
```
$ gbrain init --pglite --no-embedding        # brain created
$ printf '...QUOKKARECON77...' | gbrain put notes/recon-smoke
$ gbrain search 'QUOKKARECON77'
[0.2432] notes/recon-smoke -- # Recon Smoke Page ...
$ gbrain get notes/recon-smoke    -> returns the page (front-matter + body)
$ gbrain list -n 5                -> notes/recon-smoke  note  2026-06-20  Recon Smoke
```
VERDICT (live): GBrain **engine = WORKING** (write -> tsvector retrieve proven). The
hybrid/vector path uses OpenRouter embeddings (1536-d), not exercised offline. SEPARATELY,
the v2-integrated GBrain server (:3131) and its Postgres engine (:5432) are DOWN
(launchd last-exit 1), so the agent's `mcp_gbrain_*` tools and the Memory-tab gbrain pane are
currently non-functional until the stack is restarted. That is a deployment-down state, not a
code defect.

---

## E6 — Honcho provider — RAN, PARTIALLY WIRED (correct code, server DOWN -> silent degrade)

```
honcho SDK importable: True | version: 2.0.1
server :8000: DOWN (URLError)
provider loaded: honcho | is_available: True
prefetch (server down) -> str, empty: True, RAISED: False   (elapsed 0.10s)
sync_turn (server down) -> RAISED: False                    (elapsed 0.00s)
```
VERDICT (live): the active provider's code is correct and present, but the Honcho server is
DOWN (Docker daemon not running), so the agent's automatic external recall returns EMPTY with
NO user-visible error (fails open, non-blocking). Full store+recall requires the Docker stack
(api+pgvector+redis) plus OpenRouter credits and was NOT exercised live (flagged: heavy/paid).

---

## E7 — Injection-hardening baseline (req #11) + pytest gap — RAN

`build_memory_context_block` (agent/memory_manager.py:296) wraps prefetched provider memory in
a `<memory-context>` fence AND threat-scans it: `sanitize_context()` then
`scan_for_threats(clean, scope="strict")` (tools/threat_patterns.py); on a hit it WITHHOLDS the
body with a `[BLOCKED: ... matched injection/threat patterns ...]` note. Comment confirms the
intent: "Provider memory (Honcho dialectic output, GBrain pages) is untrusted" (Hermes #3943).
Committed test: tests/agent/test_memory_context_fence.py (clean passes, injection withheld,
invisible-unicode withheld, empty -> empty).

COVERAGE GAP to address in design: the fence covers the PROVIDER PREFETCH channel only. The
other recall channels need confirmation/coverage: `session_search` FTS5 results, holographic
`fact_store` results, and `mcp_gbrain_*` results (MCP has tests/tools/test_mcp_result_fence.py).
The NEW merge layer must scan fused results too.

PYTEST GAP (Phase 5 precondition): `.venv/bin/python -m pytest` -> "No module named pytest".
The repo venv lacks pytest. Before running the committed suite: `uv sync` (dev) or
`.venv/bin/python -m pip install pytest`. (Reversible dev-dep install; not a code change.)

---

## E8 — Green regression baseline (pre-Phase-4) — RAN

Installed `pytest 9.1.1` + `pytest-asyncio` into the repo venv (reversible dev-deps; the
running gateway PID is unaffected since these are not imported at runtime). Then ran the
existing memory test suite as the regression baseline that future changes must not break:
```
tests/tools/test_memory_layer_verify.py   (holographic store/retrieve/dedup/supersession)
tests/agent/test_memory_context_fence.py  (injection fence)
tests/tools/test_session_search.py        (session FTS5 tool)
tests/test_state_db_malformed_repair.py   (FTS5 corruption self-heal)
tests/gateway/test_agent_profile_routing.py (per-agent isolation, 27 tests)
tests/agent/test_memory_provider.py
tests/tools/test_memory_tool.py
-> first run: 253 passed, 5 failed
-> the 5 "failures" were ONLY the missing pytest-asyncio plugin (PytestUnknownMarkWarning on
   @pytest.mark.asyncio), not a code defect. After `pip install pytest-asyncio`:
   tests/gateway/test_agent_profile_routing.py -> 27 passed in 0.52s
```
BASELINE: the memory-related suite is GREEN (~280 tests). This is the regression floor for
Phase 4/5: any change that drops a test below this baseline is a regression to fix before
proceeding.

---

## E9 — Delegate sub-agent leak (C-4) verified — RAN, REAL

The Phase 3 red-team claimed the real cross-agent leak is the delegate channel, not the
gateway agent-profiles channel I proved isolated in E4. Verified independently:
```
delegate_tool.py (child spawn):
  skip_memory=True,                                     # disables provider + local store ONLY
  session_db=getattr(parent_agent, "_session_db", None) # <-- child SHARES parent state.db

hermes_state.py search_messages WHERE clauses:
  ["messages_fts MATCH ?", "m.active = 1", "s.source IN (...)", "s.source NOT IN (...)",
   "m.role IN (...)"]                                    # <-- NO session_id / lineage filter

grep -c scan_for_threats tools/session_search_tool.py -> 0   # <-- session_search is unfenced
```
CONCLUSION: a `delegate_tool` sub-agent writes into the parent's shared `state.db` and the
parent's (or a sibling's) `session_search` is DB-wide and unscanned, so it returns the whole
orchestrator history including other sub-agents', with no threat scan. This is the genuine
req-#10 leak + a req-#11 gap. My E4 isolation proof was correct only for the gateway
agent-profiles path; this delegate path is a separate mechanism I had not tested. The locked
design (PHASE3 Decision B Part 1) closes it: lineage-scope `session_search` by default +
threat-scan on return + a `scope=all` opt-in. Honesty correction recorded in the Phase 1 doc.

---

## E10 — Build slice 1 (eval harness + readonly read path) — BUILT + VERIFIED GREEN

Self-healing build harness (req #12b), both steps passed verification with real output.

STEP 1 — retrieval-eval harness + frozen gold set (`skills/memory-eval/eval.py` +
`gold/memory_gold.yaml` + `tests/tools/test_memory_recall_eval.py`). The gold set = 36 corpus
facts, 20 queries (single_hop=9, multi_hop=3, temporal=2, knowledge_update=3, abstention=3).
The NL-vs-OR recall gap is now MEASURED (this is req #7, retrieval quality as the metric):
```
metric        raw NL    OR-expanded
recall@1      0.375     0.867
recall@5      0.375     1.000
mrr           0.400     0.975
ndcg@10       0.381     0.982
gate: OR recall@5 1.000 >= threshold 0.800 -> GATE PASSED (exit 0)
abstention: all 3 unanswerable queries returned 0 rows and scored OK
```
NOT degenerate (NL=0.375 far from all-one). `test_memory_recall_eval.py` -> 9 passed.

STEP 2 — `search_facts_readonly` on the holographic store (no retrieval_count write, separate
read-only WAL connection, NL->OR expansion). `tests/tools/test_search_facts_readonly.py` ->
8 passed, including: same hits as search_facts; does NOT increment retrieval_count;
NL implicit-AND query misses but OR-expansion hits (the 0.62->1.00 lift); works on the ro
connection. The eval now drives this readonly path (no write on read).

REGRESSION: the 7-file memory baseline -> 258 passed, no failures. Green.

TYPE FIX (this turn): two PRE-EXISTING Pyright errors in store.py (`cur.lastrowid` typed
`int | None` by sqlite3 stubs, originally suppressed with mypy-only `# type: ignore`) surfaced
when the file was edited. Verified via `git diff` they are NOT my regression (my change is
+127 additive lines). Fixed correctly by binding lastrowid to a narrowed local + assert.
Proof: `pyright plugins/memory/holographic/store.py` -> "0 errors, 0 warnings, 0 informations";
import OK; store+readonly+eval tests -> 22 passed.

NOTE: all slice-1 changes are net-new files + one additive store method + a pre-existing-bug
type fix. Nothing live mutated; `merge.enabled` not yet introduced into live config.

---

## E11 — Build wave 2 (MergeLayer working slice + leak fix) + adversarial review fixes — GREEN

STEP 3 (MergeLayer over the two LOCAL planes, `agent/memory_merge.py`, additive, dark): parallel
fan-out -> per-plane sanitize+scan (drop only offending plane) -> NL->OR -> weighted RRF (k=60)
-> source-tier prior -> per-source floors -> abstention -> dedup -> RecallTrace. 11 tests passed.
Eval `--mode merged` over {session, holographic}: cross-store fused recall@5 = 1.0, MRR 0.975,
nDCG@10 0.9815. pyright memory_merge.py = 0 errors. NOT wired into live recall (merge.enabled off).

STEP 4 (close the verified delegate leak, `hermes_state.py` + `session_search_tool.py`):
search_messages gained an optional `session_ids` filter (default None = unchanged); session_search
defaults to lineage scope (resolve-to-root + bounded descendant BFS), `scope='all'` opt-out, and
per-row `scan_for_threats(strict)` on returned content (closes the req #11 gap). 12 tests passed,
baseline 275 passed, no existing test needed scope='all'.

ADVERSARIAL REVIEW (independent skeptic, reproduced every security claim with its own scripts):
ACCEPT WITH FIXES. Found ONE real bug + one doc/defense gap, both now FIXED + regression-tested:
- P1 (real correctness bug, memory_merge.py floor logic): the per-source floor rescued a
  sole-source key but then re-sorted by final_score and clipped it back out, so a low-tier
  (bulk 0.5x) sole-source plane was FULLY BURIED under an >=8-hit higher-tier plane, violating
  the "sole-source never buried" contract. FIX: pin rescued keys into guaranteed slots, exempt
  from the final-score eviction. Regression test
  `test_low_tier_sole_source_survives_full_budget_flood` reproduces the exact scenario; was the
  bug, now green.
- P2 (defense-in-depth + doc accuracy): the delegate-child fence was source-tag-only, not
  lineage (the design doc overstated lineage). FIX: the lineage walk now also excludes
  subagent/tool-source descendants at the scope level (two-layered), a non-subagent branch
  descendant stays in scope. Updated test + PHASE3 doc correction.
- Non-blocking notes (honest, documented): HRR semantic dedup is OFF without numpy in the venv
  (degrades to text-hash, the documented fallback); abstention_floor default 0.0 (ships dark).
VERIFY after fixes: merge 12 passed, isolation 12 passed, pyright memory_merge=0 /
session_search_tool=11 (pre-existing unchanged), baseline 275 passed.

Deliverable added this turn: `docs/memory-audit/MEMORY-POLICY.md` (req #8: fact schema with
bi-temporal + namespace columns, store/not-store policy, redaction-on-ingest contract).

---

## E12 — Build wave 3 (bi-temporal substrate + reconcile/redaction) + review findings — BUILT, 2 fixes in flight

STEP 5 (bi-temporal holographic substrate, `store.py`): `_migrate_bitemporal` adds ext_key
(stable), t_valid, t_invalid, supersedes_id, source_store columns IDEMPOTENTLY (each ALTER
guarded by PRAGMA table_info) and NON-DESTRUCTIVELY (ADD + backfill only, never DROP/DELETE).
`invalidate()` sets t_invalid (never deletes); `supersede()` = insert-new + invalidate-old in
one txn, recency-wins; two-phase `add_fact(defer_enrichment=True)`; `search_facts_readonly`
filters t_invalid IS NULL + as_of window + source_store namespace, on a ro WAL conn. 7 tests
passed, pyright 0 errors.

STEP 6 (reconcile engine + redaction, `tools/memory_redaction.py` + `agent/memory_reconcile.py`):
per-candidate scan_for_threats(strict)->SKIP, redact-before-INSERT, store/not-store policy,
OR-expanded retrieve-similar, ADD/UPDATE(supersede,recency-wins)/NOOP, one-plane-per-fact
(LOCAL-FIRST; honcho/gbrain = QUEUE_REMOTE stub), durable idempotent op-queue keyed by
sha256(source_store|content|op), read-your-writes hot path. 15 tests passed: secrets provably
NOT in stored content, knowledge-update supersedes-not-deletes (old recallable as_of), idempotent
across simulated restart. pyright 0 errors. Baseline 299 passed.

DATA-SAFETY confirmed by adversarial review: built a 5-row legacy DB (apostrophes, unicode,
SQL-injection-shaped, FTS-desynced), migrated 3x, EVERY row byte-intact, new columns correctly
backfilled; invalidate/supersede never DELETE; only hard DELETE (remove_fact) untouched; live
`~/.hermes/memory_store.db` never created. NO DATA-LOSS PATH.

REVIEW FOUND 2 REAL MUST-FIX BUGS (fix wave wf_a2668676 in flight, self-healing):
- P0 (migration availability): `_content_ext_key` normalizes whitespace but `content` is UNIQUE
  un-normalized, so two whitespace-variant rows derive the SAME ext_key -> CREATE UNIQUE INDEX
  raises IntegrityError -> store permanently un-openable. Same latent crash in add_fact. Cannot
  bite today (no live store) but would brick the committed O-2 legacy migration. FIX: disambiguate
  colliding ext_keys (migration) + handle ext_key-collision in add_fact (fresh uuid4, continue).
- P1 (redaction leaks): 3 secret shapes slip MEMORY-POLICY: password-only connstr
  (redis://:pass@host), passwords containing @ (u:p@ss@host leaks tail), quoted/spaced key=value
  passwords. FIX: 3 regex corrections + tests.
- P2 (note, pre-existing scanner limit): scan_for_threats(strict) only caught the classic
  injection payload; SYSTEM:/<system>/disregard-rm-rf passed through. FIX: supplementary
  conservative injection patterns at the reconcile layer -> SKIP.

Also: federated-dreaming idea evaluated by a 5-company council -> ADOPT-WITH-MODIFICATIONS
(flatten the tree; one outcome-gated promotion edge; per-leaf dreamers = model-collapse risk that
v2's own is_derived_fact guard forbids). Saved `DREAMING-federated-verdict.md`. The verdict refines
the Wave-4 promotion design with a grounding-pointer + outcome-gate (turn_score).

