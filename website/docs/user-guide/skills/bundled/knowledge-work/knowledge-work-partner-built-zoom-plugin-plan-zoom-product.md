---
title: "Kw Partner Built Plan Zoom Product"
sidebar_label: "Kw Partner Built Plan Zoom Product"
description: "Choose the right Zoom building surface for a use case and explain the tradeoffs clearly"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Plan Zoom Product

Choose the right Zoom building surface for a use case and explain the tradeoffs clearly. Use when deciding between REST API, Webhooks, WebSockets, Meeting SDK, Video SDK, Zoom Apps SDK, Phone, Contact Center, or MCP for a specific product idea or integration goal.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/plan-zoom-product` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /plan-zoom-product

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/plan-zoom-product/../../CONNECTORS.md).

Choose between Zoom REST API, Webhooks, WebSockets, Meeting SDK, Video SDK, Zoom Apps SDK, Phone, Contact Center, or MCP for a specific use case.

## Usage

```text
/plan-zoom-product $ARGUMENTS
```

## Workflow

1. Identify the user's actual goal.
2. Classify whether the problem is automation, embedded meetings, custom video, in-client app behavior, event delivery, AI tooling, or support/phone/contact-center work.
3. If the request is ambiguous, ask one short clarifier before locking the recommendation.
4. Recommend the primary Zoom surface and list the minimum supporting pieces.
5. Explain why the rejected alternatives are worse for this case.
6. End with a concrete next-step plan.

## Output

- Recommended Zoom surface
- Supporting components required
- Key tradeoffs and constraints
- Suggested implementation sequence
- Relevant skill links for the next step

## Related Skills

- [start](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/plan-zoom-product/../start/SKILL.md)
- [choose-zoom-approach](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/plan-zoom-product/../choose-zoom-approach/SKILL.md)
- [design-mcp-workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/plan-zoom-product/../design-mcp-workflow/SKILL.md)
