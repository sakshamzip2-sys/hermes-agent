---
title: "Kw Small Business Handle Complaint"
sidebar_label: "Kw Small Business Handle Complaint"
description: "Handles an incoming customer complaint end-to-end - pulls context, drafts a response, and suggests an operational fix"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Small Business Handle Complaint

Handles an incoming customer complaint end-to-end - pulls context, drafts a response, and suggests an operational fix. Accepts optional email or ticket ID argument.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/small-business/skills/handle-complaint` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Run the complaint resolution workflow by chaining two skills. Read the complaint, gather context, draft a response, and suggest a fix so it doesn't happen again.

Parse arguments:
- `EMAIL_OR_TICKET_ID` (optional) - Gmail thread ID, HubSpot ticket ID, or "latest" to pull the most recent unresolved complaint. If omitted, ask the owner to paste the complaint text.

## Step 1 - Load the complaint (ticket-deflector)

Using the `ticket-deflector` skill workflow:

1. If an ID was given: pull the full thread from Gmail or HubSpot.
2. If "latest": pull the most recent unresolved HubSpot ticket or Gmail thread tagged as complaint/support.
3. If neither: ask the owner to paste the complaint text directly.
4. Identify: customer name, order/account info, what they're upset about, what they're asking for.

## Step 2 - Pull context

1. Search HubSpot for the customer's history: past purchases, prior complaints, deal stage, lifetime value.
2. Search PayPal for relevant transaction: order status, refund history, dispute status.
3. Summarize: "This is a &#123;new/returning&#125; customer, $&#123;lifetime_value&#125; in purchases, &#123;0/N&#125; prior complaints. Their current issue is &#123;one sentence&#125;."

## Step 3 - Draft response (ticket-deflector)

Using the `ticket-deflector` skill workflow for tone-matched response:

1. Draft a reply matched to the severity and the customer's history:
   - First-time complainers with high LTV → empathetic, generous
   - Repeat complainers → professional, firm, solution-focused
   - Abusive tone → professional, brief, boundary-setting
2. Include: acknowledgment, explanation (if known), resolution offer, next step.
3. Present the draft to the owner. Do NOT send.

## Step 4 - Suggest operational fix (customer-pulse)

1. Check if this complaint matches a known theme (from prior `/customer-pulse-check` runs or similar complaints in HubSpot).
2. If it's a pattern: "This is the &#123;Nth&#125; complaint about &#123;issue&#125; this month. Consider: &#123;specific operational change&#125;."
3. If it's isolated: "This looks like a one-off. No pattern detected."

## Connector failures

If Gmail and HubSpot are both unreachable, ask the owner to paste the complaint text - the skill works with manual input. If PayPal is missing, skip transaction lookup and note "PayPal not connected - order status unavailable, working from complaint text only."

## Approval gates

- **Never send a response without explicit owner approval.** Drafts only.
- **Never issue refunds or credits automatically.** Present the option; the owner decides.
- **Never close tickets or resolve disputes without owner confirmation.**

## Output

Present the customer context summary, the drafted response, and any pattern-based operational suggestion. Ask: "Want to send this response, edit it, or handle it differently?"
