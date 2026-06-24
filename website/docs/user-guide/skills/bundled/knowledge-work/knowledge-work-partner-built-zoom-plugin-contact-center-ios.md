---
title: "Kw Partner Built Contact Center Ios — Zoom Contact Center SDK for iOS"
sidebar_label: "Kw Partner Built Contact Center Ios"
description: "Zoom Contact Center SDK for iOS"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Contact Center Ios

Zoom Contact Center SDK for iOS. Use for native iOS chat/video/ZVA/scheduled callback integrations, app lifecycle bridging, rejoin flow, and callback handling.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Contact Center SDK - iOS

Official docs:
- https://developers.zoom.us/docs/contact-center/ios/
- https://marketplacefront.zoom.us/sdk/contact/ios/index.html

## Quick Links

1. [concepts/sdk-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/concepts/sdk-lifecycle.md)
2. [examples/service-patterns.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/examples/service-patterns.md)
3. [references/ios-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/references/ios-reference-map.md)
4. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/troubleshooting/common-issues.md)

## SDK Surface Summary

- Manager: `ZoomCCInterface.sharedInstance()`
- Context: `ZoomCCContext`
- Items: `ZoomCCItem`
- Services:
- `chatService`
- `zvaService`
- `videoService`
- `scheduledCallbackService`

## Hard Guardrails

- Set `ZoomCCContext` before channel operations.
- Forward app lifecycle calls (`appDidBecomeActive`, `appDidEnterBackgroud`, `appWillResignActive`, `appWillTerminate`).
- Use item-based initialization for channels.
- Keep rejoin URL handling connected to the video service path.

## Common Chains

- Contact Center apps in Zoom client: [../../zoom-apps-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/../../zoom-apps-sdk/SKILL.md)
- OAuth and identity: [../../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/../../oauth/SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/RUNBOOK.md) - 5-minute preflight and debugging checklist.
