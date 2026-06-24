---
title: "Kw Partner Built Zoom Video Sdk React Native — Zoom Video SDK for React Native"
sidebar_label: "Kw Partner Built Zoom Video Sdk React Native"
description: "Zoom Video SDK for React Native"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Zoom Video Sdk React Native

Zoom Video SDK for React Native. Use when building custom mobile video session experiences
with @zoom/react-native-videosdk, event listeners, helper-based APIs, and backend JWT token flows.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Video SDK (React Native)

Use this skill for React Native apps that need fully custom video session experiences using Zoom Video SDK.

## Quick Links

1. **[Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/lifecycle-workflow.md)** - init -> listeners -> join -> helpers -> leave -> cleanup
2. **[SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/sdk-architecture-pattern.md)** - provider + helper model
3. **[High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/high-level-scenarios.md)** - common mobile product patterns
4. **[Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/setup-guide.md)** - package + platform setup baseline
5. **[Session Join Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/session-join-pattern.md)** - tokenized join flow
6. **[Event Handling Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/event-handling-pattern.md)** - event listener to state routing
7. **[SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/SKILL.md)** - complete navigation

## Core Notes

- Video SDK sessions are not Zoom Meetings and use session tokens.
- JWT generation must stay backend-side.
- Wrapper is helper-heavy (audio/video/chat/share/recording/transcription, etc.).
- Event-driven design is required for robust UI state.

## References

- [React Native Reference Index](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/react-native-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/module-map.md)
- [Official Sources](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/official-sources.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/troubleshooting/deprecated-and-contradictions.md)

## Related Skills

- [zoom-video-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/../SKILL.md)
- [zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/../../oauth/SKILL.md)
- [zoom-general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/../../general/SKILL.md)

## Documentation Index

### Start Here

1. [SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/SKILL.md)
2. [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/lifecycle-workflow.md)
3. [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/sdk-architecture-pattern.md)
4. [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/setup-guide.md)

### Concepts

- [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/lifecycle-workflow.md)
- [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/sdk-architecture-pattern.md)
- [High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/concepts/high-level-scenarios.md)

### Examples

- [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/setup-guide.md)
- [Session Join Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/session-join-pattern.md)
- [Event Handling Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/examples/event-handling-pattern.md)

### References

- [React Native Reference Index](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/react-native-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/module-map.md)
- [Official Sources](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/references/official-sources.md)

### Troubleshooting

- [Common Issues](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/troubleshooting/common-issues.md)
- [Version Drift](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/troubleshooting/version-drift.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/troubleshooting/deprecated-and-contradictions.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/video-sdk/react-native/RUNBOOK.md) - 5-minute preflight and debugging checklist.
