---
title: "Cron Scheduling"
sidebar_label: "Cron Scheduling"
description: "Use when the user wants to schedule, automate, or manage recurring or timed tasks: 'run this every morning', 'schedule a daily report', 'every Monday at 9', ..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Cron Scheduling

Use when the user wants to schedule, automate, or manage recurring or timed tasks: 'run this every morning', 'schedule a daily report', 'every Monday at 9', 'set a reminder to', 'list my scheduled jobs / crons / automations', 'pause that job', 'resume the schedule', 'stop running X daily', or 'delete/remove that cron'. Covers the full cron set. Deleting a job is destructive and is confirmed before it runs.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/cron-scheduling` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Cron scheduling

Manage scheduled/recurring tasks via the `cronjob` tool.

- Create: `cronjob(action="create", ...)` with the schedule + the task prompt.
- List: `cronjob(action="list")` (read-only, run autonomously).
- Pause / resume: `cronjob(action="pause"|"resume", id=...)` (reversible).
- Run now: `cronjob(action="run", id=...)`.
- DELETE (destructive): `cronjob(action="remove", id=...)`. State exactly which
  job and that removal is permanent, then proceed. The system will also surface a
  confirmation card before it executes; that is a backstop, not a substitute for
  telling the user first.

Report the result by name (e.g. "Paused cron job morning-brief").
