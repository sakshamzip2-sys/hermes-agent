---
title: "Kw Partner Built Start — Start here for any Zoom integration or app idea"
sidebar_label: "Kw Partner Built Start"
description: "Start here for any Zoom integration or app idea"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Start

Start here for any Zoom integration or app idea. Use when you need to choose the right Zoom surface, shape the architecture, or route into the correct implementation skill without reading the whole Zoom doc set first.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/start` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Start

Use this as the default entry skill for the plugin.

## What This Skill Does

- Classifies the request by job-to-be-done, not by product name alone
- Routes into the right implementation skill
- Pulls in product-specific Zoom references only after the route is clear
- Prevents common early mistakes, especially Meeting SDK vs Video SDK and REST API vs MCP confusion

## Routing Table

| If the user wants to... | Route to |
|---|---|
| Choose the right Zoom surface for a new project | [plan-zoom-product](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../plan-zoom-product/SKILL.md) |
| Set up OAuth, tokens, scopes, or app credentials | [setup-zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../setup-zoom-oauth/SKILL.md) |
| Embed or customize a Zoom meeting flow | [build-zoom-meeting-app](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../build-zoom-meeting-app/SKILL.md) |
| Build a bot, recorder, or real-time meeting processor | [build-zoom-bot](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../build-zoom-bot/SKILL.md) |
| Use Zoom-hosted MCP for AI workflows | [setup-zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../setup-zoom-mcp/SKILL.md) |
| Debug a broken integration | [debug-zoom](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../debug-zoom/SKILL.md) |

## Supporting Zoom References

Use these only after selecting the workflow:

- [general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../general/SKILL.md)
- [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../rest-api/SKILL.md)
- [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../meeting-sdk/SKILL.md)
- [video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../video-sdk/SKILL.md)
- [webhooks](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../webhooks/SKILL.md)
- [websockets](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../websockets/SKILL.md)
- [oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../oauth/SKILL.md)
- [zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/start/../zoom-mcp/SKILL.md)

## Operating Rules

1. Prefer one clear recommendation over a product catalog dump.
2. Ask a short clarifier only when the route is genuinely ambiguous.
3. Keep the first response architectural and actionable, then go deep.
4. Pull in deeper references only when they directly help the current decision or implementation.
