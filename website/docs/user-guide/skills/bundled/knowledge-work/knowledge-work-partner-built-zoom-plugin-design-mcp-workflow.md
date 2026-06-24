---
title: "Kw Partner Built Design Mcp Workflow — Design a Zoom MCP workflow for Claude"
sidebar_label: "Kw Partner Built Design Mcp Workflow"
description: "Design a Zoom MCP workflow for Claude"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Design Mcp Workflow

Design a Zoom MCP workflow for Claude. Use when deciding whether Zoom MCP fits a task, when planning tool-based AI workflows, or when separating MCP responsibilities from REST API responsibilities.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/design-mcp-workflow` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Design MCP Workflow

Use this skill when the user wants Claude or another MCP-capable client to interact with Zoom via tool calls instead of only deterministic API code.

## Covers

- MCP fit assessment
- REST API vs MCP boundaries
- Hybrid architectures
- Connector expectations
- Whiteboard-specific MCP routing

## Workflow

1. Decide whether the problem is agentic tooling, deterministic automation, or both.
2. Route MCP-only tasks to [zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/design-mcp-workflow/../zoom-mcp/SKILL.md).
3. Route hybrid tasks to both [zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/design-mcp-workflow/../zoom-mcp/SKILL.md) and [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/design-mcp-workflow/../rest-api/SKILL.md).
4. If Whiteboard is central, route to [zoom-mcp/whiteboard](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/design-mcp-workflow/../zoom-mcp/whiteboard/SKILL.md).
5. Call out transport, auth, and client capability assumptions explicitly.

## Common Mistakes

- Using MCP for deterministic backend jobs that should stay in REST
- Treating MCP as a replacement for all API design
- Ignoring client transport support and auth requirements
