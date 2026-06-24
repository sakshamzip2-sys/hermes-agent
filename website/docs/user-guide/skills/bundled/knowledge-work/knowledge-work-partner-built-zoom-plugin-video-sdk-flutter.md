---
title: "Kw Partner Built Zoom Video Sdk Flutter — Zoom Video SDK for Flutter"
sidebar_label: "Kw Partner Built Zoom Video Sdk Flutter"
description: "Zoom Video SDK for Flutter"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Zoom Video Sdk Flutter

Zoom Video SDK for Flutter. Use when building custom video session apps in Flutter with
flutter_zoom_videosdk, event-driven architecture, session lifecycle handling, and mobile
platform integration patterns.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Video SDK (Flutter)

Use this skill for Flutter apps that build custom real-time video session experiences with Zoom Video SDK.

## Quick Links

1. **[Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/lifecycle-workflow.md)** - init -> joinSession -> media/control -> leave -> cleanup
2. **[SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/sdk-architecture-pattern.md)** - helper-based API surface and event model
3. **[High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/high-level-scenarios.md)** - common product patterns
4. **[Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/setup-guide.md)** - package setup + platform prerequisites
5. **[Session Join Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/session-join-pattern.md)** - tokenized session join flow
6. **[Event Handling Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/event-handling-pattern.md)** - listener mapping and action routing
7. **[SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/SKILL.md)** - complete navigation

## Core Notes

- Video SDK sessions are custom sessions, not Zoom Meetings.
- Keep SDK credentials server-side; generate JWT token on backend.
- Integration is strongly event-driven; bind listener flows early.
- Feature support and enum names can drift by wrapper/native version.

## References

- [Flutter Reference Index](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/flutter-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/module-map.md)
- [Official Sources](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/official-sources.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/troubleshooting/deprecated-and-contradictions.md)

## Related Skills

- [zoom-video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/../SKILL.md)
- [zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/../../oauth/SKILL.md)
- [zoom-general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/../../general/SKILL.md)


## Merged from video-sdk/flutter/SKILL.md

# Zoom Video SDK Flutter - Documentation Index

## Start Here

1. [SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/SKILL.md)
2. [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/lifecycle-workflow.md)
3. [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/sdk-architecture-pattern.md)
4. [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/setup-guide.md)

## Concepts

- [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/lifecycle-workflow.md)
- [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/sdk-architecture-pattern.md)
- [High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/concepts/high-level-scenarios.md)

## Examples

- [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/setup-guide.md)
- [Session Join Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/session-join-pattern.md)
- [Event Handling Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/examples/event-handling-pattern.md)

## References

- [Flutter Reference Index](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/flutter-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/module-map.md)
- [Official Sources](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/references/official-sources.md)

## Troubleshooting

- [Common Issues](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/troubleshooting/common-issues.md)
- [Version Drift](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/troubleshooting/version-drift.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/troubleshooting/deprecated-and-contradictions.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/flutter/RUNBOOK.md) - 5-minute preflight and debugging checklist.
