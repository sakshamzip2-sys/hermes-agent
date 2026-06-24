---
title: "Kw Partner Built Contact Center Android — Zoom Contact Center SDK for Android"
sidebar_label: "Kw Partner Built Contact Center Android"
description: "Zoom Contact Center SDK for Android"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Contact Center Android

Zoom Contact Center SDK for Android. Use for native Android chat/video/ZVA/scheduled callback integrations, campaign mode, service lifecycle, and rejoin handling.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Contact Center SDK - Android

Official docs:
- https://developers.zoom.us/docs/contact-center/android/
- https://marketplacefront.zoom.us/sdk/contact/android/index.html

## Quick Links

1. [concepts/sdk-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/concepts/sdk-lifecycle.md)
2. [examples/service-patterns.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/examples/service-patterns.md)
3. [references/android-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/references/android-reference-map.md)
4. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/troubleshooting/common-issues.md)

## SDK Surface Summary

- SDK manager: `ZoomCCInterface`
- Channel services:
- `getZoomCCChatService()`
- `getZoomCCVideoService()`
- `getZoomCCZVAService()`
- `getZoomCCScheduledCallbackService()`
- Campaign support via web campaign service and campaign metadata.

## Hard Guardrails

- Initialize SDK in `Application.onCreate`.
- Use `ZoomCCItem` to define channel + identifiers.
- Use `entryId` for chat/video/ZVA.
- Use `apiKey` for scheduled callback and campaign mode.
- Release services on teardown.

## Common Chains

- Contact Center app and engagement context: [../../zoom-apps-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/../../zoom-apps-sdk/SKILL.md)
- Contact Center API automation: [../../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/../../rest-api/SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/RUNBOOK.md) - 5-minute preflight and debugging checklist.
