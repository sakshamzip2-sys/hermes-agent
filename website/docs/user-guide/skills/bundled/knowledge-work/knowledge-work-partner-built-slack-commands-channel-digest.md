---
title: "Kw Partner Built Channel Digest — Get a digest of recent activity across multiple Slack channels"
sidebar_label: "Kw Partner Built Channel Digest"
description: "Get a digest of recent activity across multiple Slack channels"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Channel Digest

Get a digest of recent activity across multiple Slack channels

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/slack/commands/channel-digest` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Given the comma-separated channel names provided in $ARGUMENTS (strip leading `#` and whitespace from each):

1. Parse the argument into individual channel names. Strip leading `#` and whitespace from each name.

2. For each channel:
   a. Use `slack_search_channels` to find the channel ID.
   b. Use `slack_read_channel` to read recent messages (use a limit of 50 messages per channel to keep things manageable).
   c. Summarize the key activity in that channel: main topics, decisions, questions, and notable messages.

3. Present the digest in this format:

   ```
   *Channel Digest - <today's date>*

   *#channel-1*
   - Summary point 1
   - Summary point 2

   *#channel-2*
   - Summary point 1
   - Summary point 2

   ...
   ```

4. For each channel, keep the summary to 3-5 bullet points maximum. Focus on what's actionable or noteworthy.

5. If a channel has no recent activity, note that it's been quiet and mention when the last message was posted (if visible).

6. If a channel name can't be found, let the user know and continue with the remaining channels.
