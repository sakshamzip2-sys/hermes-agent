---
title: "Roll Forward"
sidebar_label: "Roll Forward"
description: "Build a roll-forward schedule for a balance-sheet account, beginning balance plus activity less reversals equals ending balance, with each component tied to GL"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Roll Forward

Build a roll-forward schedule for a balance-sheet account, beginning balance plus activity less reversals equals ending balance, with each component tied to GL. Use for month-end close packages and audit support.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/finance/roll-forward` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Roll-forward

Given an account (or account group), entity, and period, produce a roll-forward that ties beginning to ending.

## Structure

```
Beginning balance (per prior-period close)      X
  + Additions / new activity                    A
  + Accruals booked this period                 B
  − Reversals of prior accruals                (C)
  − Payments / settlements                     (D)
  ± Reclasses / adjustments                     E
  ± FX translation                              F
Ending balance (per GL at period end)           Y
```

## Tie each line

- **Beginning**, prior-period close package, or GL balance at prior-period end date.
- **Each activity line**, a GL query (account + date range + journal-source filter) via the internal-gl MCP. Cite the query.
- **Ending**, GL balance at period-end date.

The schedule **must foot**: `X + A + B − C − D + E + F = Y`. If it doesn't, the gap is an unexplained item, surface it, don't plug it.

## Output

The roll-forward table with a "ties to" column citing the GL query or document for every line, plus a foot check (pass/fail and the unexplained delta if any).
