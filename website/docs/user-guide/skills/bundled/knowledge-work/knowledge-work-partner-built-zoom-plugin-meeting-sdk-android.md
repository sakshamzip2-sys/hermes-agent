---
title: "Kw Partner Built Zoom Meeting Sdk Android — Zoom Meeting SDK for Android native apps"
sidebar_label: "Kw Partner Built Zoom Meeting Sdk Android"
description: "Zoom Meeting SDK for Android native apps"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Zoom Meeting Sdk Android

Zoom Meeting SDK for Android native apps. Use when embedding Zoom meetings in Android with
default/custom UI, PKCE + SDK auth, join/start flows, and Meeting SDK API integration.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Meeting SDK (Android)

Use this skill when building Android apps with embedded Zoom meeting capabilities.

## Start Here

1. [android.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/android.md)
2. [concepts/lifecycle-workflow.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/concepts/lifecycle-workflow.md)
3. [concepts/architecture.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/concepts/architecture.md)
4. [examples/join-start-pattern.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/examples/join-start-pattern.md)
5. [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/scenarios/high-level-scenarios.md)
6. [references/android-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/references/android-reference-map.md)
7. [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/references/environment-variables.md)
8. [references/versioning-and-compatibility.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/references/versioning-and-compatibility.md)
9. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/troubleshooting/common-issues.md)

## Routing Notes

- Use **default UI** first for first successful join/start validation.
- Move to **custom UI** once auth, meeting state transitions, and permissions are stable.
- For signature/JWT mistakes, chain with [../../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/../../oauth/SKILL.md) and [../references/signature-playbook.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/../references/signature-playbook.md).

## Key Sources

- Docs: https://developers.zoom.us/docs/meeting-sdk/android/
- API reference: https://marketplacefront.zoom.us/sdk/meeting/android/index.html
- Broader guide: [../SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/../SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/android/RUNBOOK.md) - 5-minute preflight and debugging checklist.
