---
title: "Variance Commentary"
sidebar_label: "Variance Commentary"
description: "Write flux commentary for every P&L and balance-sheet line over threshold, current vs prior period and vs budget, with the driver explained from underlying a..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Variance Commentary

Write flux commentary for every P&L and balance-sheet line over threshold, current vs prior period and vs budget, with the driver explained from underlying activity. Use for the month-end close package and management reporting.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/finance/variance-commentary` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Variance commentary

Given current-period actuals, prior-period actuals, and budget for the same scope, produce a commentary table.

## Threshold

Flag a line for commentary if **either** is true:

- Absolute variance ≥ the firm's materiality threshold (use the provided value; default 5% of the line or a fixed floor, whichever is greater)
- The line is on the "always comment" list (revenue, headcount cost, cash)

## For each flagged line

| Column | Content |
|---|---|
| **Line** | Account or caption |
| **Current / Prior / Budget** | The three values |
| **Δ vs prior** and **Δ vs budget** | Amount and % |
| **Driver** | One sentence explaining the movement from underlying activity, not a restatement of the number |

A driver explains *why*, not *what*: "Cloud spend up $1.2M on incremental GPU reservations for the May launch", not "Cloud spend increased $1.2M (18%)."

## Sourcing the driver

Look at the activity behind the line (journal-source breakdown, vendor mix, headcount delta, volume × rate) via the internal-gl MCP. If the driver isn't clear from the data, write "driver unclear, flag for controller" rather than inventing one.

## Output

The commentary table plus a short narrative (3–5 sentences) summarizing the period's biggest movers.
