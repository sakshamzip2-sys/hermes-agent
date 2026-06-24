---
title: "Kw Small Business Tax Prep"
sidebar_label: "Kw Small Business Tax Prep"
description: "Prepares tax-season materials - quarterly estimated tax calculation or year-end 1099 prep - and produces an accountant handoff packet"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Small Business Tax Prep

Prepares tax-season materials - quarterly estimated tax calculation or year-end 1099 prep - and produces an accountant handoff packet. Accepts optional mode and year arguments.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/small-business/skills/tax-prep` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Run the tax prep workflow using the `tax-season-organizer` skill. Act immediately - the user typed /tax-prep, so skip the discovery phase.

Parse arguments:
- `--mode` (default: infer from date - Q1-Q3 defaults to `quarterly`, Q4/Jan defaults to `both`) - `quarterly` for estimated tax payment, `1099` for year-end 1099-NEC prep, `both` for combined
- `--year` (default: current year)

**Framing:** Open every deliverable with "Prepared for review by your accountant - not tax advice."

## Step 1 - Determine mode

If `--mode` was not provided:
1. Check the current date. If Oct–Jan, default to `both`. Otherwise default to `quarterly`.
2. Confirm with the owner: "Based on the time of year, I'll prepare [mode]. Want me to do something different?"

## Step 2 - Quarterly estimated tax (if mode includes quarterly)

1. Pull YTD Profit & Loss from QuickBooks (Jan 1 through last completed quarter).
2. If QuickBooks is not connected, ask the user to paste net income or upload a CSV.
3. Ask: "How much have you already paid in estimated taxes this year?"
4. Calculate: SE tax, adjusted net income, federal income tax estimate (default 22% bracket), quarterly payment due.
5. State every assumption explicitly - bracket, business type, exclusions.
6. Deliver the formatted estimate with the due date for the current quarter.

## Step 3 - Year-end 1099 prep (if mode includes 1099)

1. Pull contractor/vendor payments from all connected sources: QuickBooks, PayPal, Stripe.
2. Aggregate by payee across sources. Flag likely duplicates for human review - never auto-merge.
3. Apply the $600 threshold. Flag near-threshold payees ($400–$599).
4. Check W-9 status in QuickBooks for each flagged payee.
5. Deliver the 1099-NEC candidate list with missing W-9 action items and the PayPal/Stripe 1099-K overlap note.

## Approval gates

- **Not tax advice.** State this in every output header.
- **State every assumption.** Bracket, business type, excluded deductions - give the accountant the levers.
- **Don't merge payees automatically.** Flag duplicates for human review.
- **Don't file anything.** Output is prep material only.

## Output

End with a next-steps checklist for the accountant: missing W-9s to collect, assumptions to verify, deadlines to hit.
