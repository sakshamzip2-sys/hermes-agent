---
title: "Kw Partner Built Build Zoom Virtual Agent — Reference skill for Zoom Virtual Agent"
sidebar_label: "Kw Partner Built Build Zoom Virtual Agent"
description: "Reference skill for Zoom Virtual Agent"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Build Zoom Virtual Agent

Reference skill for Zoom Virtual Agent. Use after routing to a virtual-agent workflow when implementing web embeds, Android or iOS wrapper integrations, knowledge-base sync, lifecycle handling, or troubleshooting.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /build-zoom-virtual-agent

Background reference for Zoom Virtual Agent across:
- Web campaign/chat embeds.
- Android WebView wrappers.
- iOS WKWebView wrappers.
- Knowledge-base sync and custom API ingestion.

Official docs:
- https://developers.zoom.us/docs/virtual-agent/
- https://developers.zoom.us/docs/virtual-agent/web/
- https://developers.zoom.us/docs/virtual-agent/android/
- https://developers.zoom.us/docs/virtual-agent/ios/

## Routing Guardrail

- If the user is implementing Contact Center app surfaces inside Zoom client, chain with [../contact-center/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../contact-center/SKILL.md).
- If the user needs backend knowledge-base CRUD or automation scripts, chain with [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../rest-api/SKILL.md) and [../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../oauth/SKILL.md).
- If the user asks only for website bot embed and campaign controls, stay on [web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/web/SKILL.md).
- If the user asks for mobile native wrappers around web chat, route to [android/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/SKILL.md) or [ios/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/SKILL.md).

## Quick Links

1. [concepts/architecture-and-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/concepts/architecture-and-lifecycle.md)
2. [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/scenarios/high-level-scenarios.md)
3. [references/versioning-and-drift.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/references/versioning-and-drift.md)
4. [references/samples-validation.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/references/samples-validation.md)
5. [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/references/environment-variables.md)
6. [troubleshooting/common-drift-and-breaks.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/troubleshooting/common-drift-and-breaks.md)
7. [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/RUNBOOK.md)

Platform skills:
- [web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/web/SKILL.md)
- [android/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/SKILL.md)
- [ios/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/SKILL.md)

## Common Lifecycle Pattern

1. Configure campaign or entry ID in Virtual Agent admin.
2. Initialize SDK in web or WebView container.
3. Wait for readiness (`zoomCampaignSdk:ready` or `waitForReady()`) before calling APIs.
4. Register bridge handlers (`exitHandler`, `commonHandler`, `support_handoff`) when native orchestration is needed.
5. Handle conversation lifecycle (`engagement_started`, `engagement_ended`) and UI state.
6. End chat (`endChat`) and clean up listeners.

## High-Level Scenarios

- Website campaign launcher with contextual customer attributes.
- Mobile app WebView chat with native close/handoff bridge.
- External URL handling via system browser vs in-app browser policy.
- Knowledge-base sync from external systems using custom API connector.
- Cross-team support flow that escalates from bot to live support with handoff payload.

## Chaining

- Contact Center app/web/mobile patterns: [../contact-center/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../contact-center/SKILL.md)
- OAuth app setup and tokens: [../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../oauth/SKILL.md)
- API workflows for KB automation: [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../rest-api/SKILL.md)
- Event-driven backend follow-up: [../webhooks/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/../webhooks/SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/RUNBOOK.md) - 5-minute preflight and debugging checklist.
