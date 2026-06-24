---
title: "Kw Partner Built Zoom Meeting Sdk React Native — Zoom Meeting SDK for React Native"
sidebar_label: "Kw Partner Built Zoom Meeting Sdk React Native"
description: "Zoom Meeting SDK for React Native"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Zoom Meeting Sdk React Native

Zoom Meeting SDK for React Native. Use when embedding Zoom meetings in React Native iOS/Android apps with @zoom/meetingsdk-react-native, JWT auth, join/start flows, platform setup, and native bridge troubleshooting.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Meeting SDK (React Native)

Use this skill when building React Native apps that need embedded Zoom meeting join/start flows.

## Quick Links

1. **[Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/lifecycle-workflow.md)** - init -> auth -> join/start -> in-meeting -> cleanup
2. **[Architecture](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/architecture.md)** - JS wrapper, native bridge, iOS/Android SDK layers
3. **[High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/high-level-scenarios.md)** - practical product patterns
4. **[Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/setup-guide.md)** - install package + platform requirements
5. **[Join Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/join-meeting-pattern.md)** - JWT + meetingNumber + password
6. **[Start Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/start-meeting-pattern.md)** - ZAK-based host start
7. **[SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/SKILL.md)** - full navigation

## Core APIs (Wrapper)

From `@zoom/meetingsdk-react-native` wrapper surface:

- `initSDK(config)`
- `isInitialized()`
- `updateMeetingSetting(config)`
- `joinMeeting(config)`
- `startMeeting(config)`
- `cleanup()`

See: **[Wrapper API](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/wrapper-api.md)**

## Critical Notes

- You still need native iOS/Android Meeting SDK dependencies configured.
- `joinMeeting` and `startMeeting` return numeric status/error codes from native layer.
- For host start flow, pass `zoomAccessToken` (ZAK).
- Keep JWT generation on backend, never embed SDK secret in app.
- Current docs note React Native support up to `0.75.4`; Expo is not supported.

## Platform Guides

- **[iOS Setup](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/ios-setup.md)** - Podfile, optional ReplayKit/app group fields
- **[Android Setup](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/android-setup.md)** - Gradle dependency + options mapping
- **[Native Bridge Notes](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/native-bridge-notes.md)** - behavior differences and gotchas

## Troubleshooting

- **[Common Issues](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/common-issues.md)**
- **[Version Drift](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/version-drift.md)**
- **[Deprecated/Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/deprecated-and-contradictions.md)**

## Related Skills

- **[zoom-meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/../SKILL.md)** - parent Meeting SDK hub
- **[zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/../../oauth/SKILL.md)** - auth flow and token management
- **[zoom-general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/../../general/SKILL.md)** - cross-product architecture decisions

## Documentation Index

### Start Here

1. [SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/SKILL.md)
2. [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/lifecycle-workflow.md)
3. [Architecture](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/architecture.md)
4. [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/setup-guide.md)

### Concepts

- [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/lifecycle-workflow.md)
- [Architecture](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/architecture.md)
- [Auth and Token Model](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/auth-and-token-model.md)
- [High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/concepts/high-level-scenarios.md)

### Examples

- [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/setup-guide.md)
- [Join Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/join-meeting-pattern.md)
- [Start Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/start-meeting-pattern.md)
- [Provider Hook Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/examples/provider-hook-pattern.md)

### References

- [Wrapper API](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/wrapper-api.md)
- [Android Setup](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/android-setup.md)
- [iOS Setup](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/ios-setup.md)
- [Native Bridge Notes](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/native-bridge-notes.md)
- [Official Sources](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/references/official-sources.md)

### Troubleshooting

- [Common Issues](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/common-issues.md)
- [Version Drift](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/version-drift.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/troubleshooting/deprecated-and-contradictions.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/react-native/RUNBOOK.md) - 5-minute preflight and debugging checklist.
