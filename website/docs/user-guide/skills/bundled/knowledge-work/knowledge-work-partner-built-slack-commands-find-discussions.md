---
title: "Kw Partner Built Find Discussions — Find discussions about a specific topic across Slack channels"
sidebar_label: "Kw Partner Built Find Discussions"
description: "Find discussions about a specific topic across Slack channels"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Find Discussions

Find discussions about a specific topic across Slack channels

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/slack/commands/find-discussions` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

Given the topic provided in $ARGUMENTS:

1. Use the `slack_search_public` tool to search for messages matching the topic. Use the topic as a natural language question first for semantic results.
2. If semantic results are sparse, follow up with a keyword search using key terms from the topic.
3. For the most relevant results, use `slack_read_thread` to fetch full thread conversations so you capture the complete discussion context.
4. Present the results organized by relevance:
   - For each discussion found, show: the channel name, who started it, a brief summary of the conversation, and the date.
   - Group related discussions together if they span multiple channels.
   - Highlight any conclusions, decisions, or unresolved questions.
5. Limit output to the top 5-10 most relevant discussions to keep results manageable.
6. If no results are found, suggest alternative search terms or broader queries the user could try.
