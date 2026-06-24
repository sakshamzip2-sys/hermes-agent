---
title: "Commercial Legal Review Proposals"
sidebar_label: "Commercial Legal Review Proposals"
description: "Review and approve (or reject) pending playbook update proposals from the playbook-monitor agent and apply approved changes to the practice profile"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Commercial Legal Review Proposals

Review and approve (or reject) pending playbook update proposals from the playbook-monitor agent and apply approved changes to the practice profile. Use when the playbook-monitor agent has surfaced proposals, when the user says "review playbook proposals", "what playbook updates are pending", or wants to step through deviation-driven playbook changes.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/commercial-legal/review-proposals` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /review-proposals

Steps through pending playbook update proposals from the monitor agent and applies approved changes to `~/.hermes/legal-practice-profile/commercial-legal/CLAUDE.md`.

## Instructions

1. **Load the playbook-monitor agent** and run Step 5 (review and approval flow).

2. **If no proposals file exists** or it is empty: respond *"No pending proposals. Playbook is up to date."* Do not proceed further.

3. **Present proposals one at a time.** For each, show the full proposal block and offer four options: Accept, Reject, Edit, Defer.

4. **For Accept or Edit:** show the exact diff to `~/.hermes/legal-practice-profile/commercial-legal/CLAUDE.md` before writing. Only apply after the attorney explicitly confirms.

5. **For Reject or Defer:** log the decision. Do not modify `~/.hermes/legal-practice-profile/commercial-legal/CLAUDE.md`.

6. **After all proposals are resolved:** show a summary of what changed, then archive the proposals file.

## Examples

```
/commercial-legal:review-proposals
```

```
/commercial-legal:review-proposals
(runs automatically after playbook-monitor notifies you)
```
