---
title: "Dreaming — Consolidate recent sessions into long-term memory"
sidebar_label: "Dreaming"
description: "Consolidate recent sessions into long-term memory"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Dreaming

Consolidate recent sessions into long-term memory.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/dreaming` |
| Version | `1.0.0` |
| Platforms | linux, macos, windows |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Dreaming Skill

Dreaming is automatic memory consolidation: it distils durable, user-specific facts
from recent conversations and promotes the high-signal ones into long-term memory
(`MEMORY.md`) so they're recalled in future sessions. It is provided by the
`dreaming` plugin and runs on its own — this skill explains how to drive it manually
and how to reason about what it does.

## When to Use

- The user asks to "consolidate memory", "remember what we discussed", "run dreaming",
  or wonders why something was/wasn't saved to long-term memory.
- After a long or important session, to force a consolidation pass immediately rather
  than waiting for the next automatic cycle.
- To inspect the `DREAMS.md` holding pen (facts seen once that haven't yet earned a
  promotion).

## Prerequisites

- The `dreaming` plugin must be enabled (it registers the `/dream` command and the
  `dreaming` auxiliary task). Check with `opencomputer dream status`.
- An auxiliary model for the `dreaming` task (config `auxiliary.dreaming`) is needed
  for fact extraction/scoring. Without one, dreaming safely promotes nothing.

## How to Run

Run a consolidation pass now (bypasses the debounce interval):

```
opencomputer dream run --force
```

Or in a conversation: `/dream run force`. Inspect status / the holding pen:

```
opencomputer dream status
opencomputer dream dreams
```

## Quick Reference

| Command | Effect |
|---------|--------|
| `opencomputer dream status` | config, last-run time, recent pass counts |
| `opencomputer dream run` | one consolidation pass (respects debounce) |
| `opencomputer dream run --force` | consolidation pass now, ignoring debounce |
| `opencomputer dream dreams` | list the `DREAMS.md` holding pen |
| `/dream`, `/dream run`, `/dream dreams` | same, in-session |

## How It Works

Each candidate fact passes three gates before promotion (defaults shown):

1. **Importance** — an auxiliary LLM scores durability/usefulness; must be ≥ `0.65`.
2. **Recall** — how many distinct past sessions touched this topic; must be ≥ `2`
   (set `dreaming.recall_gate_enabled: false` to disable on low-history profiles).
3. **Diversity** — not a near-duplicate of an existing `MEMORY.md` entry.

Passing all three → promoted to `MEMORY.md` (marked `(dreamed DATE)`). Passing
diversity but failing score/recall → held in `DREAMS.md`, re-scored on later runs as
recall grows. Failing diversity → dropped, or (if a contradiction) it *supersedes*
the stale entry in place.

Config lives under `dreaming:` in `config.yaml`; the model under `auxiliary.dreaming`.

## Pitfalls

- **Nothing gets promoted on a fresh profile.** The recall gate needs a topic to recur
  across ≥2 sessions. That's intentional (social proof) — disable the recall gate or
  lower `min_recall_count` if you want eager promotion.
- **Dreaming writes to `MEMORY.md` automatically.** It only runs when the plugin is
  installed/enabled — installing the plugin is the opt-in.
- **It never blocks a turn.** Automatic runs happen on a background thread, debounced
  to `dreaming.min_interval_hours` (default 6h).

## Verification

`opencomputer dream run --force` prints the per-pass counts and each promoted fact; confirm
new `(dreamed …)` entries with `opencomputer dream status` and by reading `MEMORY.md`.
