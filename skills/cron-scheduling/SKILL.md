---
name: cron-scheduling
destructive: true
description: "Use when the user wants to schedule, automate, or manage recurring or timed tasks: 'run this every morning', 'schedule a daily report', 'every Monday at 9', 'set a reminder to', 'list my scheduled jobs / crons / automations', 'pause that job', 'resume the schedule', 'stop running X daily', or 'delete/remove that cron'. Covers the full cron set. Deleting a job is destructive and is confirmed before it runs."
---

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
