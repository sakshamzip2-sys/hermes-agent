---
title: "Employment Legal Investigation Memo — Draft or update the privileged investigation memo from the investigation log"
sidebar_label: "Employment Legal Investigation Memo"
description: "Draft or update the privileged investigation memo from the investigation log"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Investigation Memo

Draft or update the privileged investigation memo from the investigation log. Use when an investigation is far enough along to write the first memo cut, or when new data has been added and the existing draft needs updating.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/investigation-memo` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /investigation-memo

Drafts the first cut of the privileged investigation memo from the log,
or updates an existing draft when new data has been added.

## Instructions

1. Load the `internal-investigation` reference skill and run Mode 4 (Draft or update memo).
2. If drafting for the first time, warn if high-priority sources are still
   open on the checklist.
3. If updating, show what changed before rewriting.
4. All output is marked PRIVILEGED AND CONFIDENTIAL -- ATTORNEY WORK PRODUCT.

## Examples

```
/employment-legal:investigation-memo [matter name]
```

```
/employment-legal:investigation-memo [matter name]
(updates existing memo if one exists)
```

> Detailed memo structure, credibility-assessment framework, and update rules
> live in the `internal-investigation` reference skill -- load it before doing
> substantive work.
