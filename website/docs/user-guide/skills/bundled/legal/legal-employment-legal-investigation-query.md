---
title: "Employment Legal Investigation Query"
sidebar_label: "Employment Legal Investigation Query"
description: "Ask questions against an open investigation log -- what witnesses said, where accounts conflict, what gaps exist, what the strongest evidence is on each issue"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Investigation Query

Ask questions against an open investigation log -- what witnesses said, where accounts conflict, what gaps exist, what the strongest evidence is on each issue. Use when the attorney needs to query the investigation record without re-reading every entry.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/investigation-query` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /investigation-query

Answers questions against the investigation log -- what witnesses said,
where accounts conflict, what gaps exist, what the strongest evidence is
on each issue.

## Instructions

1. Load the `internal-investigation` reference skill and run Mode 3 (Query).
2. Always cite log entry IDs in the answer.
3. If the log contains nothing relevant to the question, say so explicitly --
   "I have not seen any information on [topic] in this investigation log
   ([N] entries reviewed)" -- and offer to flag it as a gap.

## Examples

```
/employment-legal:investigation-query [matter name]
What did the respondent say about the December team dinner?
```

```
/employment-legal:investigation-query [matter name]
Where do the complainant's and respondent's accounts conflict?
```

```
/employment-legal:investigation-query [matter name]
What do we still need?
```

> Detailed log-query process, citation rules, and gap-flagging templates live
> in the `internal-investigation` reference skill -- load it before doing
> substantive work.
