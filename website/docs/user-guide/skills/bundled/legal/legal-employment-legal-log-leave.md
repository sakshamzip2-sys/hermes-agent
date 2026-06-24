---
title: "Employment Legal Log Leave"
sidebar_label: "Employment Legal Log Leave"
description: "Add a new leave to the leave register with the minimum information needed to start tracking deadlines"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Employment Legal Log Leave

Add a new leave to the leave register with the minimum information needed to start tracking deadlines. Use when an employee goes on leave and you want the tracker to watch designation, certification, and exhaustion clocks from day one.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/employment-legal/log-leave` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /log-leave

Adds a new leave entry to `~/.hermes/legal-practice-profile/employment-legal/leave-register.yaml` with the minimum
information needed to start tracking deadlines. Use when an employee goes on
leave and you want the tracker to watch the clocks from day one.

## Instructions

1. Read `~/.hermes/legal-practice-profile/employment-legal/CLAUDE.md` → jurisdiction table and Systems section.

2. Ask all of the following in a single prompt -- do not drip them one at a time:

   > A few quick questions to set up leave tracking:
   >
   > - Employee name or role (anonymized is fine)
   > - Where do they work? (State -- this determines which rules apply)
   > - Leave type: FMLA / state leave (which state) / USERRA / ADA accommodation
   > - Leave start date
   > - Is this intermittent leave?
   > - Expected return date (if known -- leave blank if not)
   > - Has the designation notice been sent? If yes, when?
   > - Has medical certification been requested? If yes, when?

3. Using the jurisdiction table in `~/.hermes/legal-practice-profile/employment-legal/CLAUDE.md`, look up the applicable leave
   entitlement (hours/weeks) for this leave type in this jurisdiction.

4. Compute the first upcoming deadline based on the information provided:
   - Designation not yet sent → deadline is 5 business days from leave start
   - Med cert requested but not received → deadline is 15 days from request date
   - Both sent and received → next deadline is at 75% exhaustion

5. Write a new entry to `~/.hermes/legal-practice-profile/employment-legal/leave-register.yaml` using the leave register
   format from the leave-tracker agent. If the file doesn't exist, create it.

6. Confirm with a single line:
   > "Logged. [Employee/Role] -- [Leave type] -- [Jurisdiction] -- started [date].
   > First deadline: [what it is and when]. Leave tracker will alert automatically."

## Examples

```
/employment-legal:log-leave
```

```
/employment-legal:log-leave
Sarah (Sr. Engineer, works in California) just started FMLA today for a
serious health condition. Intermittent. No designation sent yet.
```
