# dreaming — memory consolidation plugin

Ports OpenComputer **v1's** three-gate "dreaming" pipeline into **v2** (hermes-agent)
as a self-contained, edge plugin (no new core tool, no core-file edits).

## What it does

Distils durable, user-specific facts from recent session history and promotes the
high-signal ones into `MEMORY.md`. Each candidate passes three gates — **importance**
(aux-LLM score ≥ 0.65), **recall** (resurfaced across ≥ 2 sessions), and **diversity**
(not a near-duplicate) — then is promoted, held in `DREAMS.md`, or dropped. A
high-similarity contradiction can *supersede* a stale entry in place.

## Files

| File | Role |
|------|------|
| `engine.py` | Pure three-gate engine (faithful v1 port; injectable callables) |
| `candidates.py` | Reads `state.db` session turns → digests; FTS-based recall proxy |
| `llm.py` | Aux-LLM extract/score/supersede + offline lexical diversity embedder |
| `memory_io.py` | `MEMORY.md`/`DREAMS.md` promote / hold / replace / re-score I/O |
| `store.py` | SQLite idempotency ledger + last-run + audit |
| `config.py` | Loads `dreaming:` block from config.yaml (v1-matching defaults) |
| `runner.py` | Orchestration: DREAMS re-score pass + new-session pass |
| `cli.py` | `opencomputer dream {status,run,dreams}` |
| `__init__.py` | `register(ctx)` — aux task, session hooks, slash + CLI commands |

## How v2 differs from v1 (intentional adaptations)

- **Candidate source.** v1 dreamed over a pre-summarised `episodic_events` table; v2 has
  none, so this reads raw turns from `state.db` (`messages`) and **extracts** facts with
  an aux LLM before the gates.
- **Recall signal.** v1 used a `recall_citations` table; v2 approximates "did this
  resurface?" with an FTS5 count of distinct sessions matching the fact's salient terms
  (toggle with `recall_gate_enabled`).
- **Diversity embedder.** Defaults to a zero-dependency lexical (term-frequency cosine)
  embedder — catches near-verbatim duplicates without a network embeddings backend.
- **Trigger.** v1 ran on a 60s system tick; v2 runs opportunistically on session-boundary
  hooks (debounced) + manual `/dream` / `opencomputer dream run`.

## Ported from v1's enhancement set

- **DREAMS.md re-scoring** — held facts graduate as recall accumulates (`runner._rescore_dreams`).
- **Semantic embeddings** (`llm.semantic_embed`) — opt-in via `dreaming.embed_model`; calls
  the aux provider's `/embeddings` endpoint, **falls back to the lexical embedder** on any
  failure/absence (so the diversity gate stays deterministic offline).
- **Clustering pre-gate** (`cluster.py`) — greedy single-linkage (cosine ≥
  `cluster_similarity_threshold`, default 0.7) collapses near-duplicate facts beyond the
  exact-text dedup; keyword/lexical fallback when no embedder.
- **Cron-miss catch-up** — a long gap since the last run widens the fetch limit for one
  recovery pass so an outage doesn't silently drop facts.

## Honestly deferred (not faked)

- **Outcome-driven threshold tuning** (v1 `dreaming_outcomes.py`) — needs a per-turn
  `turn_score` signal v2 does not capture; wiring it would touch core, out of scope for an
  edge plugin. Documented, not stubbed.
- **Review-mode queue + HMAC rollback** (v1 `dreaming_review.py`) — a human approval gate
  before auto-writing `MEMORY.md`. Partially mitigated today by the opt-in install + the
  `DREAMS.md` holding pen; the full HMAC queue is the next addition.
- **Bias-amplification detection** and **external-memory sinks** (Honcho reconcile).

## Config

```yaml
dreaming:
  enabled: true
  min_interval_hours: 6
  score_threshold: 0.65
  min_recall_count: 2
  diversity_threshold: 0.8
  max_promotions_per_run: 20
  dreams_md_max_bytes: 16384
  candidate_fetch_limit: 50
  supersede_enabled: true
  recall_gate_enabled: true

auxiliary:
  dreaming:
    provider: auto      # pin a cheap model for consolidation
    model: ""
```

## Tests

`tests/plugins/test_dreaming_*.py` — 63 tests (engine routing, runner end-to-end,
memory I/O, store, candidates+recall proxy, config, llm). Run:

```
.venv/bin/python -m pytest tests/plugins/test_dreaming_*.py -q -p no:cacheprovider
```
