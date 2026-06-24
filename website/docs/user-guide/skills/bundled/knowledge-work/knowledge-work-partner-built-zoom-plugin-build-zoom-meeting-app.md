---
title: "Kw Partner Built Build Zoom Meeting App — Build or embed a Zoom meeting flow"
sidebar_label: "Kw Partner Built Build Zoom Meeting App"
description: "Build or embed a Zoom meeting flow"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Build Zoom Meeting App

Build or embed a Zoom meeting flow. Use when implementing Meeting SDK joins, web or mobile meeting embeds, meeting lifecycle flows, or when deciding between Meeting SDK and Video SDK.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /build-zoom-meeting-app

Use this skill for embedded meeting experiences and meeting lifecycle implementation.

## Covers

- Meeting SDK selection and platform routing
- Join/auth implementation planning
- Meeting creation plus join flow design
- Web vs native platform considerations
- Meeting SDK vs Video SDK boundary decisions

## Workflow

1. Confirm whether the user wants a Zoom meeting or a custom video session.
2. Route to Meeting SDK if the user needs actual Zoom meetings.
3. Pull in the relevant platform references.
4. Add REST API only for meeting creation, resource management, or reporting.
5. Add webhooks or RTMS only when the use case explicitly needs them.

## Primary References

- [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app/../meeting-sdk/SKILL.md)
- [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app/../rest-api/SKILL.md)
- [webhooks](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app/../webhooks/SKILL.md)
- [rtms](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app/../rtms/SKILL.md)
- [video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-meeting-app/../video-sdk/SKILL.md)

## Common Mistakes

- Using Video SDK for normal Zoom meeting embeds
- Mixing resource-management APIs into the core join flow without reason
- Skipping platform-specific SDK constraints until too late
