---
title: "Kw Partner Built Weekly Brief — Generate a weekly prep briefing from your calendar and Common Room"
sidebar_label: "Kw Partner Built Weekly Brief"
description: "Generate a weekly prep briefing from your calendar and Common Room"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Weekly Brief

Generate a weekly prep briefing from your calendar and Common Room

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/common-room/commands/weekly-brief` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Generate a weekly prep briefing using Common Room and your calendar.

Follow the weekly-prep-brief skill:
1. Use the ~~calendar connector to retrieve all external customer-facing meetings scheduled for the next 7 days (or the date range specified in "$ARGUMENTS"). Filter out internal meetings - focus on calls with customers, prospects, or partners.
2. If no ~~calendar connector is available, ask the user to list their external calls (company name, date, attendees).
3. For each external meeting, run account research and contact research on attendees in parallel.
4. Compile into a single weekly briefing: week overview + per-meeting sections sorted by date.

Keep each per-meeting section tight and scannable. Total briefing should be readable in under 10 minutes.
