---
title: "Kw Partner Built Setup Zoom Mcp — Decide when Zoom MCP is the right fit and produce a safe setup plan for Claude"
sidebar_label: "Kw Partner Built Setup Zoom Mcp"
description: "Decide when Zoom MCP is the right fit and produce a safe setup plan for Claude"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Setup Zoom Mcp

Decide when Zoom MCP is the right fit and produce a safe setup plan for Claude. Use when planning AI workflows over Zoom data, deciding between MCP and REST, or defining a hybrid MCP architecture.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-mcp` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /setup-zoom-mcp

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-mcp/../../CONNECTORS.md).

Plan a Zoom MCP workflow and decide when to use MCP alone versus a hybrid REST API + MCP architecture.

## Usage

```text
/setup-zoom-mcp $ARGUMENTS
```

## Workflow

1. Determine whether the goal is deterministic automation, AI tool orchestration, or a hybrid.
2. If MCP is appropriate, identify the likely Zoom MCP surface and transport assumptions.
3. If MCP alone is not enough, define the REST API responsibilities separately.
4. Call out auth, scope, and client capability constraints.
5. End with a minimal proof-of-concept sequence.

## Output

- Recommended MCP strategy
- Connector expectations
- Hybrid boundaries if REST is also required
- Risks and setup notes
- Relevant skill links

## Related Skills

- [design-mcp-workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-mcp/../design-mcp-workflow/SKILL.md)
- [choose-zoom-approach](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-mcp/../choose-zoom-approach/SKILL.md)
