---
title: "Kw Partner Built Debug Zoom"
sidebar_label: "Kw Partner Built Debug Zoom"
description: "Debug a broken Zoom integration by isolating the failure point and routing into the right Zoom references"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Debug Zoom

Debug a broken Zoom integration by isolating the failure point and routing into the right Zoom references. Use when auth, API, webhook, SDK, or MCP behavior is failing and you need a ranked hypothesis list plus verification steps.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /debug-zoom

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom/../../CONNECTORS.md).

Debug Zoom auth, API, webhook, SDK, or MCP issues without wandering through the entire docs set.

## Usage

```text
/debug-zoom $ARGUMENTS
```

## Workflow

1. Identify the failing layer: auth, API request, webhook, SDK init, media/session behavior, or MCP transport.
2. Ask for the minimum missing evidence: exact error, platform, request/response, event payload, or code path.
3. Produce 2-4 plausible causes ranked by likelihood.
4. Route to the most relevant deep references in `skills/`.
5. Give a short verification plan so the user can confirm the fix.

## Output

- Most likely failure layer
- Ranked hypotheses
- Targeted fix steps
- Verification checklist
- Relevant skill links

## Related Skills

- [debug-zoom-integration](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom/../debug-zoom-integration/SKILL.md)
- [setup-zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom/../setup-zoom-oauth/SKILL.md)
- [design-mcp-workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/debug-zoom/../design-mcp-workflow/SKILL.md)
