---
title: "Competitor Analysis — Competitor research and intelligence skill"
sidebar_label: "Competitor Analysis"
description: "Competitor research and intelligence skill"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Competitor Analysis

Competitor research and intelligence skill. Takes a user's company (with optional
seed competitor URLs), auto-discovers additional competitors via the Browserbase
Search API, deeply researches each using a multi-lane pattern (marketing surface,
external signal, public benchmarks, strategic diff vs the user's company), and
compiles the results into an HTML report with four views: overview, per-competitor
deep dive, side-by-side feature/pricing matrix, and a chronological mentions feed
(news, reviews, social, comparison pages, and public benchmarks).
Use when the user wants to: (1) analyze competitors, (2) build a competitive matrix,
(3) extract competitor pricing / features, (4) find comparison pages and online
mentions of competitors, (5) surface public benchmarks. Triggers: "competitor analysis",
"analyze competitors", "competitive intel", "competitor research", "competitor pricing",
"feature comparison", "price comparison", "find comparisons", "who's comparing us",
"competitor mentions", "competitor benchmarks".

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/research/competitor-analysis` |
| Version | `0.2.0` |
| License | MIT |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Competitor Analysis

Discover, research, and report on a company's competitors using cloud browsing
(Browserbase) for all web I/O and parallel subagents for breadth.

> Source: `browserbase/skills` (MIT). Ported to OpenComputer v2. The original was
> authored for Claude Code; the v2 mapping of its agent primitives is in
> **"v2 integration notes"** at the bottom — read that first if anything below
> references Claude-Code tools.

## Setup (one-time)

1. `npm install -g browse` (the Browserbase CLI).
2. Export `BROWSERBASE_API_KEY` (the skill refuses to run without it).
3. All web access goes through `browse cloud search` / `browse cloud fetch` /
   `browse open --remote` + `browse get markdown`. **Never** use a generic web-fetch
   tool here — Browserbase is the only sanctioned web surface so sessions, captchas,
   and rate limits are handled.

## Pipeline (8 steps)

The verbatim recipes, subagent prompt templates, the noise-domain blocklist, the
per-competitor `.md` format, and the report scaffold live in `references/`. Read the
referenced file at the start of each step.

- **Step 0 — Workspace.** Create `~/Desktop/{slug}_competitors_{YYYY-MM-DD}/` with
  `partials/` inside. `slug` = the user's company name, lowercased/kebab-cased.
- **Step 1 — Self-research the user's company** (5 lanes). Establish the baseline,
  derive a `precise_category`, and write category exclusion rules. See
  `references/research-patterns.md` ("Self-Research") and persist a confirmed profile
  to `profiles/{slug}.json`.
- **Step 2 — Depth + seeds.** Ask the user (via the ask-user primitive) for depth
  mode — `quick` / `deep` / `deeper` — and any seed competitor URLs.
- **Step 3 — Discovery** (3 parallel `browse cloud search` waves):
  (a) "&#123;category&#125; alternatives", (b) precise-category queries, (c) an "X vs Y"
  comparison graph. Aim for ~3× the target candidate count. See
  `references/workflow.md`. Use `scripts/extract_vs_names.mjs` and
  `scripts/list_urls.mjs` to dedup.
- **Step 4 — Gate.** `node scripts/gate_candidates.mjs --include ... --exclude ...`
  filters candidates by category fit (title-based PASS/REJECT with a hero-text
  tiebreak).
- **Step 4.5 — Confirm (mandatory gate).** Present the gated set to the user and get
  explicit confirmation BEFORE the expensive enrichment. Never enrich an unconfirmed
  set.
- **Step 5 — Enrichment.** For each confirmed competitor launch **5 parallel lane
  subagents** (marketing / discussion / social / news / technical), each writing a
  partial. Then:
  - `node scripts/merge_partials.mjs` → canonical `{slug}.md` per competitor.
  - Synthesize a shared-taxonomy `matrix.json` (features / pricing / integrations).
  - Spot-check only high-stakes matrix cells (≈25-call fact-check budget), then
    rewrite win/loss summaries post-verification.
  - **Step 5d (deep/deeper only) — Battle Cards.** Synthesis-only layer grounded in
    the partials + verified cells. See `references/battle-card.md` and
    `references/battle-card-subagent.md`.
- **Step 6 — Screenshots.** `node scripts/capture_screenshots.mjs --mode remote`
  (or `local`) → 1280×800 hero PNG per competitor.
- **Step 7 — Report.** `node scripts/compile_report.mjs --user-company "{name}" --open`
  builds the 4 HTML views (overview / competitors / matrix / mentions) + `results.csv`
  from the `.md` files and `matrix.json`. Template: `references/report-template.html`.

## Pitfalls / failure modes to avoid

- Enriching before the Step 4.5 user confirmation (burns budget on wrong companies).
- Using a non-Browserbase web tool (breaks session handling / gets blocked).
- Per-cell fact-checking everything (blows the call budget — spot-check high-stakes only).
- Letting each competitor invent its own feature taxonomy — synthesize ONE shared
  taxonomy across all companies so the matrix is comparable.
- One write/search per subagent call — batch writes into a single bash heredoc and
  batch searches to minimize approvals.

## Success criteria

A dated workspace folder containing one `.md` per competitor, a `matrix.json` with
sourced cells, hero screenshots, and a self-contained HTML report (4 views) + CSV the
user can open in a browser.

## v2 integration notes

This skill was authored for Claude Code. In OpenComputer v2:

- **Parallel "Agent" subagents → `delegate_task`.** Each competitor×lane research lane
  becomes one `delegate_task` child; launch a wave as a single batched
  `delegate_task(tasks=[...])` call (bounded by `delegation.max_concurrent_children`,
  default 3 — raise it for `deep`/`deeper` if you want more lanes in flight). These
  children are **leaf** role (pure research, no further delegation needed).
- **`AskUserQuestion` → the `clarify` tool** for Step 2 (depth/seeds) and Step 4.5
  (confirm competitor set).
- **`Bash` is unchanged** — `browse` and `node scripts/*.mjs` run as normal shell.
- Browserbase is an explicit, opt-in external dependency (like the `parallel-cli`
  vendor skill). It does not make the core agent vendor-locked; it only activates when
  this skill runs and `BROWSERBASE_API_KEY` is present.
