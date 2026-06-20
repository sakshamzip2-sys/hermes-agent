---
name: memory-eval
description: "Score the memory layer's RETRIEVAL QUALITY (does the right fact actually surface?) against a frozen BEIR-style gold set, and gate releases on it - distinct from agent-eval (which scores tool/output behavior). Use before/after any change to the memory stores, retrieval path, or merge layer; when you want to assert 'recall@5 did not regress / abstention still returns nothing / the NL-vs-OR gap holds'; or when wiring a memory-quality gate into CI. Drives the real holographic MemoryStore on a temp DB and reports recall@k, precision@k, hit-rate@k, MRR, nDCG@10 per query and aggregate."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  opencomputer:
    tags: [eval, memory, retrieval, recall, precision, ndcg, mrr, regression, ci, quality-gate]
    related_skills: [agent-eval, verification-before-completion]
---

# Memory Retrieval-Quality Eval Harness

`agent-eval` scores the agent's BEHAVIOR (which tool it picks, what it leaks).
This skill scores the MEMORY LAYER's RETRIEVAL QUALITY: given a frozen gold set
of facts and queries, does the right fact actually surface for a query? "Stored
it and got something back" is NOT success (requirement #7 of the memory mission);
the metric is recall@k / precision@k / MRR / nDCG against a per-query relevant set.

This is the GATE the rest of the memory rebuild must clear. The merge layer, the
bi-temporal write path, and the remote planes all ship behind `memory.merge.enabled:
false` until this eval clears its frozen floor.

## When to use

- Before/after touching any memory store, the retrieval path, or the merge layer.
- When you want to assert non-negotiables: recall@5 did not drop below the floor,
  abstention queries still return nothing, the NL-vs-OR gap still holds.
- To wire a memory-quality gate into CI.

NOT for: scoring the agent's tool/output behavior (use `agent-eval`); benchmarking
the base model (use `evaluating-llms-harness`).

## How it works

1. **Gold set** (`gold/memory_gold.yaml`) is a frozen, BEIR-style ground truth:
   ~35 OpenComputer-flavored `corpus` facts (each with a stable `id`) and ~20
   `queries`, each listing the `relevant_fact_ids` it should retrieve. It spans
   the five query types: `single_hop`, `multi_hop`, `temporal`, `knowledge_update`
   (the newer fact is relevant; the superseded one is `allowed` but not required),
   and `abstention` (empty relevant set - returning nothing is the correct answer).
   It folds in the NL-vs-OR cases from `memory-stack/recall_probe.py` and a
   RULER-style scale-stress case (one needle fact among look-alike distractors).
   No secrets / API keys / PII (requirement #8).

2. **The harness** (`eval.py`) builds a fresh **temp** `MemoryStore`
   (`plugins.memory.holographic.store.MemoryStore`, no `~/.hermes` mutation,
   requirement #2), inserts every corpus fact via `add_fact`, then for each query
   computes, against its `relevant_fact_ids`:

       Recall@{1,3,5,10}, Precision@{1,3,5}, Hit-Rate@{1,3,5,10}, MRR, nDCG@10

   Each query runs TWICE: once as the **raw NL** string, once **OR-expanded**
   (split, drop stopwords, join with ` OR `). FTS5 implicitly ANDs query terms,
   so NL filler words force misses; the OR pass measures the lift explicitly (the
   gap `recall_probe.py` proved: ~0.62 NL vs ~1.00 OR). This measures TODAY's
   FTS5 baseline; there is no merge layer yet.

3. **Score + gate**: `--threshold <floor>` exits non-zero when the aggregate
   recall@5 (in the `--mode`, default `or`) falls below the floor. Abstention
   queries are scored by REWARDING an empty result.

## Run

```bash
# from the skill dir or the repo root; gold path defaults to gold/memory_gold.yaml
.venv/bin/python skills/memory-eval/eval.py --threshold 0.8
echo "exit $?"   # 0 = gate passed, 1 = recall@5 regressed below the floor

.venv/bin/python skills/memory-eval/eval.py --json          # machine-readable report
.venv/bin/python skills/memory-eval/eval.py --mode nl --threshold 0.3   # gate the raw-NL pass
```

Baseline numbers on today's holographic FTS5 store (no merge layer): raw NL
recall@5 ~0.38, OR-expanded recall@5 ~1.00. That gap is the whole point of the
OR-expansion fold-in and is asserted by the regression test.

## The CI gate (regression test)

`tests/tools/test_memory_recall_eval.py` is the committed gate. It runs the gold
set through `eval.py` and asserts:

- the OR-expanded aggregate **recall@5 >= 0.8** (frozen floor, matches
  `recall_probe.RECALL_FLOOR`) - fail = a memory-layer change regressed retrieval;
- every **abstention** query returns nothing in both retrieval modes;
- the **NL-vs-OR gap** is real (raw NL recall lags OR-expanded);
- the metric primitives (recall / precision / MRR / nDCG) are correct, pinned
  against hand-computed values;
- the gold set carries no secrets / PII.

```bash
.venv/bin/python -m pytest tests/tools/test_memory_recall_eval.py -q
```

Wire it into CI exactly like `agent-eval`:

```bash
.venv/bin/python skills/memory-eval/eval.py --threshold 0.8 || exit 1
```

## Extending the gold set

Add new cases at the END of `gold/memory_gold.yaml` (never edit existing ids or
relevance once green, or the numbers stop being comparable). Each corpus fact
needs a stable `id`; each query needs `relevant_fact_ids` (and optional
`allowed_fact_ids` for superseded knowledge-update facts). Keep secrets/PII out.
When the merge layer lands, point a second gold run at it and compare cross-store
fused recall@5 to this FTS5-only baseline.

For any LLM-as-judge cases added later, use a DIFFERENT judge model than the one
in production and aggregate over the set - per-query scores are noisy.
