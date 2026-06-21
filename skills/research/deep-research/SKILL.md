---
name: deep-research
description: |
  Deep, multi-source research using NESTED ORCHESTRATION. The main agent spawns one or
  more orchestrator subagents; each orchestrator fans out ~3 parallel leaf researchers
  (each covering a distinct angle), runs an adversarial verification pass, then synthesizes
  a single cited report. Use when the user wants a thorough, fact-checked answer that one
  pass of web search can't give: market/landscape scans, technical due diligence, literature
  reviews, "compare X options across N dimensions", "research this deeply and cite sources".
  Triggers: "deep research", "research this thoroughly", "comprehensive analysis", "do a
  deep dive", "investigate and cite sources", "multi-source report", "go deep on".
version: 1.0.0
metadata:
  opencomputer:
    tags: [research, orchestration, delegation, deep-research, multi-agent]
    related_skills: [competitor-analysis, llm-wiki, research-paper-writing]
---

# Deep Research (nested orchestrator)

This skill turns one question into a fan-out research tree using v2's built-in delegation
(`delegate_task`). It is the canonical use of the **nested orchestrator** capability.

## Prerequisite (already enabled on this install)

Nesting requires `delegation.max_spawn_depth >= 2` in config. At depth 1 (flat) any
`role="orchestrator"` is silently downgraded to a leaf and the tree collapses to a single
layer. This install is configured to `2`. If a deeper tree is needed, raise it further —
but note cost scales multiplicatively (see Cost).

## The pattern

```
main agent (depth 0)
  └─ delegate_task(role="orchestrator")  ── orchestrator (depth 1)
                                              ├─ leaf: angle A   (depth 2, forced leaf)
                                              ├─ leaf: angle B
                                              └─ leaf: adversarial verify
                                           └─ orchestrator SYNTHESIZES the 3 results
```

For broad topics, the main agent spawns **several orchestrators in parallel** (one per
sub-theme), each running its own 3-leaf team — `main → 3 orchestrators → 9 leaves`.

## Steps

1. **Scope.** Restate the question, list the 3–6 sub-questions/angles it decomposes into,
   and pick a mode:
   - `quick` — 1 orchestrator, 2 leaves, no separate verify leaf.
   - `standard` (default) — 1 orchestrator, 3 leaves (2 research angles + 1 adversarial verify).
   - `deeper` — N orchestrators (one per sub-theme), each with 3 leaves.
2. **Spawn the orchestrator(s).** Call `delegate_task` with `role="orchestrator"`. Give each
   orchestrator: the sub-question, the list of leaf angles to spawn, the required output
   shape (claims + sources), and the instruction to dedupe/synthesize its leaves.
   - For `deeper`, pass `tasks=[...]` so multiple orchestrators run concurrently (bounded by
     `delegation.max_concurrent_children`, default 3 — raise if you want more in flight).
3. **Each orchestrator fans out leaves** via its own `delegate_task(tasks=[...])`. Leaf roles:
   - **Research leaves** — each takes ONE angle, uses the available web/search tools, and
     returns findings as bullet *claims, each with a source URL/citation*. Different leaves
     must not overlap angles.
   - **Adversarial-verify leaf** — receives the draft claims and tries to REFUTE them
     (find contradicting sources, check dates/numbers, flag unsourced assertions). Returns a
     keep/kill verdict per claim with evidence.
4. **Synthesize.** The orchestrator merges leaf outputs, drops refuted/unsourced claims, and
   returns a structured section. The main agent assembles all orchestrator sections into one
   report.
5. **Write the report** to `docs/research/YYYY-MM-DD-<slug>.md`: executive summary →
   per-theme findings → a "sources" list → an "open questions / low-confidence" section.
   Every non-obvious claim carries an inline citation.

## Rules

- **Model-agnostic.** Use whatever provider/model the runtime resolves; never hardcode a
  vendor. Children inherit the configured `delegation.model` (cheap by default — good for
  parallel leaves).
- **Citations are mandatory.** A claim with no source is a candidate for deletion, not a
  finding. The verify leaf exists to enforce this.
- **No overlap.** Each leaf owns a distinct angle/source-set so the fan-out adds coverage,
  not redundancy.
- **Synthesis ≠ concatenation.** The orchestrator must reconcile contradictions between
  leaves, not paste them.

## Cost

Tree size multiplies tokens: `standard` ≈ 1×(1+3) child runs; `deeper` ≈ N×(1+3). A 3×3
tree is ~12 child agents. Default to `standard`; only go `deeper` when the user asks for
exhaustive coverage or the topic genuinely has independent sub-themes.

## Success criteria

A dated, cited `docs/research/...md` report whose claims survived an adversarial verify pass,
produced by a real fan-out (visible as multiple child agents in the run), not a single-agent
web sweep.
