---
title: "Employment Legal Investigation Open"
sidebar_label: "Employment Legal Investigation Open"
description: "Open a new internal investigation matter -- runs intake, generates the sources checklist, and creates the persistent investigation log"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Investigation Open

Open a new internal investigation matter -- runs intake, generates the sources checklist, and creates the persistent investigation log. Use when a complaint or allegation comes in and the attorney needs to stand up a privileged investigation workspace.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/investigation-open` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /investigation-open

Opens a new investigation matter -- runs intake, generates the sources
checklist, and creates the persistent investigation log.

## Instructions

1. Load `~/.hermes/legal-practice-profile/employment-legal/CLAUDE.md`.
2. Load the `internal-investigation` reference skill and run Mode 1 (Open).
3. If a matter with the same slug already exists, warn before overwriting.

## Examples

```
/employment-legal:investigation-open
Harassment complaint filed against a manager in the Austin office.
```

```
/employment-legal:investigation-open
(skill will ask for details)
```

> Detailed intake, privilege-formation requirements, sources checklist, and log
> templates live in the `internal-investigation` reference skill -- load it
> before doing substantive work.
