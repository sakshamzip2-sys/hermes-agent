---
title: "Kw Partner Built Choose Zoom Approach — Choose the right Zoom architecture for a use case"
sidebar_label: "Kw Partner Built Choose Zoom Approach"
description: "Choose the right Zoom architecture for a use case"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Choose Zoom Approach

Choose the right Zoom architecture for a use case. Use when deciding between REST API, Webhooks, WebSockets, Meeting SDK, Video SDK, Zoom Apps SDK, Zoom MCP, Phone, Contact Center, or a hybrid approach.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Choose Zoom Approach

Pick the smallest correct Zoom surface for the job, then layer in only the supporting pieces that are actually required.

## Decision Framework

| Problem Type | Primary Zoom Surface |
|---|---|
| Deterministic backend automation, account management, reporting, scheduled jobs | [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../rest-api/SKILL.md) |
| Event delivery to your backend | [webhooks](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../webhooks/SKILL.md) or [websockets](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../websockets/SKILL.md) |
| Embed Zoom meetings into your app | [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../meeting-sdk/SKILL.md) |
| Build a fully custom video experience | [video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../video-sdk/SKILL.md) |
| Build inside the Zoom client | [zoom-apps-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../zoom-apps-sdk/SKILL.md) |
| AI-agent tool workflows over Zoom data | [zoom-mcp](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../zoom-mcp/SKILL.md) |
| Real-time media extraction or meeting bots | [rtms](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../rtms/SKILL.md) plus [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../meeting-sdk/SKILL.md) when needed |
| Phone workflows | [phone](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../phone/SKILL.md) |
| Contact Center or Virtual Agent flows | [contact-center](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../contact-center/SKILL.md) or [virtual-agent](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/choose-zoom-approach/../virtual-agent/SKILL.md) |

## Guardrails

- Do not recommend Video SDK when the user actually needs Zoom meeting semantics.
- Do not recommend Meeting SDK when the user needs a fully custom session product.
- Do not replace deterministic backend automation with MCP-only guidance.
- Prefer hybrid `rest-api + zoom-mcp` when the user needs both stable system actions and AI-driven discovery.

## What To Produce

- One recommended path
- Minimum supporting components
- Hard constraints and tradeoffs
- Immediate next implementation step
