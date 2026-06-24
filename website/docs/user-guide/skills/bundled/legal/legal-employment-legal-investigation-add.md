---
title: "Employment Legal Investigation Add — Add data to an open investigation -- documents, interview notes, or observations"
sidebar_label: "Employment Legal Investigation Add"
description: "Add data to an open investigation -- documents, interview notes, or observations"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Investigation Add

Add data to an open investigation -- documents, interview notes, or observations. Processes batches against the documented pull criteria, surfaces significant items, and logs everything reviewed for coverage verification. Use when new evidence, interview notes, or document productions come in for an open investigation.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/investigation-add` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /investigation-add

Adds data to an open investigation log. Processes document batches using
documented pull criteria, surfaces significant items, logs everything
reviewed for coverage verification.

## Instructions

1. Load `~/.hermes/legal-practice-profile/employment-legal/CLAUDE.md`.
2. Load the `internal-investigation` reference skill and run Mode 2 (Add data).
3. After processing, show the surface ratio and list of surfaced items.
4. Prompt to update the sources checklist if the data covers a checklist item.

## Examples

```
/employment-legal:investigation-add [matter name]
[paste interview notes]
```

```
/employment-legal:investigation-add [matter name]
[attach email export]
```

> Detailed needle-finding process, log entry format, surface-ratio rules, and
> sources-checklist tracking live in the `internal-investigation` reference
> skill -- load it before doing substantive work.
