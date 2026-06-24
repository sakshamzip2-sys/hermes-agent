---
title: "Kw Partner Built Draft Announcement — Draft a well-formatted Slack announcement and save it as a draft"
sidebar_label: "Kw Partner Built Draft Announcement"
description: "Draft a well-formatted Slack announcement and save it as a draft"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Draft Announcement

Draft a well-formatted Slack announcement and save it as a draft

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/slack/commands/draft-announcement` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Given the topic or context provided in $ARGUMENTS:

1. Ask the user the following clarifying questions (skip any that are already clear from the provided context):
   - Which channel should this announcement be posted in?
   - Who is the target audience?
   - What is the key message or call to action?
   - Is there a deadline or date to highlight?
   - What tone is appropriate - formal, casual, or urgent?

2. Compose the announcement following Slack formatting best practices:
   - Use Slack's mrkdwn syntax: `*bold*` for emphasis (not `**bold**`), `_italic_` for secondary emphasis, `>` for callouts.
   - Lead with the most important information - don't bury the point.
   - Use a clear, descriptive opening line that works as a headline.
   - Keep paragraphs short (2-3 sentences max).
   - Use bullet points for lists of items or action steps.
   - Include relevant emoji sparingly to aid scanning (e.g., :mega: for announcements, :calendar: for dates, :point_right: for action items).
   - End with a clear call to action or next step if applicable.

3. Present the draft to the user for review. Offer to adjust tone, length, or formatting.

4. Once the user approves, use `slack_search_channels` to find the target channel ID, then use `slack_send_message_draft` to create the draft in Slack.

5. Let the user know the draft is ready in Slack and they can review and send it from the Slack client.
