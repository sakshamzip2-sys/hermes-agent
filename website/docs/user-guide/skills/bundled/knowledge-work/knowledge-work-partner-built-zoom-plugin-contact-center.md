---
title: "Kw Partner Built Build Zoom Contact Center App — Reference skill for Zoom Contact Center"
sidebar_label: "Kw Partner Built Build Zoom Contact Center App"
description: "Reference skill for Zoom Contact Center"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Build Zoom Contact Center App

Reference skill for Zoom Contact Center. Use after routing to a contact-center workflow when implementing app, web, or native integrations; engagement context and state handling; campaigns; callbacks; or version-drift troubleshooting.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /build-zoom-contact-center-app

Background reference for Zoom Contact Center integrations across app, web, and native mobile surfaces.

Implementation guidance for Zoom Contact Center across:
- Contact Center apps in the Zoom client (Zoom Apps SDK path)
- Web channel embeds (chat/video/campaign)
- Native mobile SDKs (Android/iOS)

Official docs:
- https://developers.zoom.us/docs/contact-center/
- https://developers.zoom.us/docs/contact-center/web/sdk-reference/
- https://marketplacefront.zoom.us/sdk/contact/android/index.html
- https://marketplacefront.zoom.us/sdk/contact/ios/index.html

## Routing Guardrail

- If the user is building an app inside the Zoom Contact Center desktop client, stay on the Zoom Apps SDK path and use this skill plus `zoom-apps-sdk`.
- If the user is embedding chat/video widgets on a website, route to [web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/SKILL.md).
- If the user is integrating native Android or iOS SDK binaries, route to [android/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/SKILL.md) or [ios/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/SKILL.md).
- If the user needs Contact Center call-control or queue APIs, chain with [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/../rest-api/SKILL.md).

## Quick Links

Start here:
1. [concepts/architecture-and-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/concepts/architecture-and-lifecycle.md)
2. [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/scenarios/high-level-scenarios.md)
3. [references/forum-top-questions.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/references/forum-top-questions.md)
4. [references/versioning-and-compatibility.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/references/versioning-and-compatibility.md)
5. [references/samples-validation.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/references/samples-validation.md)
6. [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/references/environment-variables.md)
7. [troubleshooting/common-drift-and-breaks.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/troubleshooting/common-drift-and-breaks.md)
8. [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/RUNBOOK.md)

Platform skills:
- [android/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/android/SKILL.md)
- [ios/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/ios/SKILL.md)
- [web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/web/SKILL.md)

## Documentation Structure

<!-- ascii-guard-ignore -->
```
contact-center/
├── SKILL.md
├── RUNBOOK.md
├── concepts/
│   └── architecture-and-lifecycle.md
├── scenarios/
│   └── high-level-scenarios.md
├── references/
│   ├── versioning-and-compatibility.md
│   ├── samples-validation.md
│   └── environment-variables.md
├── troubleshooting/
│   └── common-drift-and-breaks.md
├── android/
│   ├── SKILL.md
│   ├── concepts/sdk-lifecycle.md
│   ├── examples/service-patterns.md
│   ├── references/android-reference-map.md
│   └── troubleshooting/common-issues.md
├── ios/
│   ├── SKILL.md
│   ├── concepts/sdk-lifecycle.md
│   ├── examples/service-patterns.md
│   ├── references/ios-reference-map.md
│   └── troubleshooting/common-issues.md
└── web/
    ├── SKILL.md
    ├── concepts/lifecycle-and-events.md
    ├── examples/app-context-and-state.md
    ├── references/web-reference-map.md
    └── troubleshooting/common-issues.md
```
<!-- ascii-guard-ignore-end -->

## Common Lifecycle Pattern

1. Initialize platform context early.
2. Build a channel item (`entryId` for chat/video/ZVA, `apiKey` for scheduled callback and campaign flows).
3. Get service/client instance.
4. Register listeners/delegates before user interaction.
5. Start flow (`fetchUI`, `startVideo`, or web SDK open/show path).
6. Handle engagement state changes (`start`, `hold`, `resume`, `end`) and context switching.
7. End flow and release resources (`endChat`/`endVideo`, `logout/logoff`, uninitialize/release).

## High-Level Scenarios

- Agent side-panel app that stores notes per `engagementId` and survives context switching.
- Browser chat/video campaigns launched from web tags.
- Native mobile customer app for chat/video/scheduled callback.
- Campaign-driven channel selection (chat, ZVA, video, scheduled callback).
- Rejoin flow for dropped video engagements on mobile.
- Smart Embed CRM softphone with postMessage event contracts.

See [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/scenarios/high-level-scenarios.md) for details.

## Chaining

- Auth and in-client app identity: [../zoom-apps-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/../zoom-apps-sdk/SKILL.md) and [../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/../oauth/SKILL.md)
- Contact Center REST workflows: [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/../rest-api/SKILL.md)
- Cobrowse on web voice/chat channels: [../cobrowse-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/../cobrowse-sdk/SKILL.md)

## Environment Variables

- See [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/contact-center/references/environment-variables.md) for standardized `.env` keys and where to find each value.
