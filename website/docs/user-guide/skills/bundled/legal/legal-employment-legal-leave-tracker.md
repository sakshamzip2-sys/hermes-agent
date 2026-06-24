---
title: "Employment Legal Leave Tracker — Check open leaves for deadline alerts and required decisions"
sidebar_label: "Employment Legal Leave Tracker"
description: "Check open leaves for deadline alerts and required decisions"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Leave Tracker

Check open leaves for deadline alerts and required decisions. Surfaces only the leaves that require an action and explains why -- not a status board. Use weekly, or whenever the attorney needs to know which leaves have upcoming designation, certification, or exhaustion deadlines.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/leave-tracker` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /leave-tracker

Checks all open leaves with hard legal deadlines and surfaces only the ones
requiring a decision or action. Not a status board -- tells you what you need
to do and why.

## Instructions

1. Load the `leave-tracker` agent and run the full workflow.

2. If no HRIS is connected and no `~/.hermes/legal-practice-profile/employment-legal/leave-register.yaml` exists, prompt
   the attorney to upload a leave spreadsheet or use
   `/employment-legal:log-leave` to add entries.

3. Alerts only for leaves requiring action. Clean leaves summarized one line each.

## Examples

```
/employment-legal:leave-tracker
```

Run this weekly -- set a Monday-morning reminder to invoke
`/employment-legal:leave-tracker`. Automated scheduling requires a separate
integration (calendar reminder, cron job, etc.); Claude Code agents do not
self-schedule.
