---
title: "Kw Partner Built Debug Zoom Integration — Debug broken Zoom implementations quickly"
sidebar_label: "Kw Partner Built Debug Zoom Integration"
description: "Debug broken Zoom implementations quickly"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Debug Zoom Integration

Debug broken Zoom implementations quickly. Use when auth, webhooks, SDK joins, MCP transport, or real-time media workflows are failing and you need to isolate the layer before proposing a fix.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Debug Zoom Integration

Use this skill when the user already built something and it is failing.

## Triage Order

1. Auth and app configuration
2. Request construction or event verification
3. SDK initialization or platform mismatch
4. Media/session behavior
5. MCP transport and capability assumptions

## Evidence To Request

- Exact error text
- Platform and SDK/runtime
- Relevant request or payload sample
- What worked versus what failed
- Whether the issue is reproducible or intermittent

## Reference Routing

- [oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../oauth/SKILL.md)
- [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../rest-api/SKILL.md)
- [webhooks](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../webhooks/SKILL.md)
- [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../meeting-sdk/SKILL.md)
- [video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../video-sdk/SKILL.md)
- [rtms](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../rtms/SKILL.md)
- [zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom-integration/../zoom-mcp/SKILL.md)

## Output

- Most likely failing layer
- Ranked hypotheses
- Short fix plan
- Verification steps
