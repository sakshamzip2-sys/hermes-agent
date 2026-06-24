---
title: "Regulatory Legal Gaps — Open gaps tracker -- what's flagged and not yet closed"
sidebar_label: "Regulatory Legal Gaps"
description: "Open gaps tracker -- what's flagged and not yet closed"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Regulatory Legal Gaps

Open gaps tracker -- what's flagged and not yet closed. Use when the user asks "what gaps are open", "gap tracker", "remediation status", or wants to close (--close GAP-ID) or risk-accept (--accept GAP-ID) a tracked gap.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/legal/regulatory-legal/gaps` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /gaps

1. Read the gap tracker at `~/.hermes/legal-practice-profile/regulatory-legal/gap-tracker.yaml`.
2. If `--close`: mark gap closed with resolution note.
3. If `--accept`: record the risk-acceptance rationale and acceptor, status → risk-accepted.
4. Otherwise: report open gaps by age and materiality.

> Detailed tracker schema, status-report format, owner-notification logic (per-send confirmation, no exceptions), reminder cadence, the close/risk-accept modes, and the consequential-action gate live in the **gap-surfacer** reference skill -- load it before doing substantive work.
