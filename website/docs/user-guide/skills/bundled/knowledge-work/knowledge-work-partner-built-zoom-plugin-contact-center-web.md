---
title: "Kw Partner Built Contact Center Web — Zoom Contact Center SDK for Web"
sidebar_label: "Kw Partner Built Contact Center Web"
description: "Zoom Contact Center SDK for Web"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Contact Center Web

Zoom Contact Center SDK for Web. Use for web chat/video/campaign embeds, engagement event handling, app-context integrations, and Smart Embed postMessage workflows.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Contact Center SDK - Web

Official docs:
- https://developers.zoom.us/docs/contact-center/web/
- https://developers.zoom.us/docs/contact-center/web/sdk-reference/

## Quick Links

1. [concepts/lifecycle-and-events.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/concepts/lifecycle-and-events.md)
2. [examples/app-context-and-state.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/examples/app-context-and-state.md)
3. [references/web-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/references/web-reference-map.md)
4. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/troubleshooting/common-issues.md)

## Integration Modes

1. Contact Center App in Zoom client:
- Zoom Apps SDK engagement APIs/events.

2. External website embed:
- Campaign SDK/web scripts (`zoomCampaignSdk` pattern).
- Video client initialization pattern.

3. Smart Embed:
- iframe + `postMessage` event contract.

## Hard Guardrails

- For campaign SDK, gate calls behind `zoomCampaignSdk:ready`.
- Persist state by `engagementId`.
- Expect context switching and background app behavior.
- Validate CSP and allow-list settings before debugging logic.

## Chaining

- For in-client app APIs and auth flows: [../../zoom-apps-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/../../zoom-apps-sdk/SKILL.md)
- For identity and OAuth: [../../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/../../oauth/SKILL.md)
- For cobrowse workflow: [../../cobrowse-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/../../cobrowse-sdk/SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/RUNBOOK.md) - 5-minute preflight and debugging checklist.
