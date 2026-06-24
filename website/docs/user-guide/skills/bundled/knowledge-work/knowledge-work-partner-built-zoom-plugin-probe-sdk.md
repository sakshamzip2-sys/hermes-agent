---
title: "Kw Partner Built Probe Sdk — Reference skill for Zoom Probe SDK"
sidebar_label: "Kw Partner Built Probe Sdk"
description: "Reference skill for Zoom Probe SDK"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Probe Sdk

Reference skill for Zoom Probe SDK. Use after routing to a preflight workflow when testing browser compatibility, media permissions, audio or video diagnostics, and network readiness before users join.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Probe SDK

Background reference for preflight diagnostics on user devices and networks before meeting or session workflows.

Official docs:
- https://developers.zoom.us/docs/probe-sdk/
- https://marketplacefront.zoom.us/sdk/probe/index.html

Reference sample:
- https://github.com/zoom/probesdk-web

## Routing Guardrail

- Use Probe SDK when the user needs client-side diagnostics and readiness scoring (device/network/browser capability), not meeting/session join.
- If user needs embedded meeting flows, route to [../meeting-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../meeting-sdk/SKILL.md).
- If user needs custom real-time session UX, route to [../video-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../video-sdk/SKILL.md).
- If user needs backend orchestration of events/APIs, chain with [../rivet-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../rivet-sdk/SKILL.md), [../oauth/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../oauth/SKILL.md), and [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../rest-api/SKILL.md).

## Quick Links

Start here:
1. [probe-sdk.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/probe-sdk.md)
2. [concepts/architecture-and-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/concepts/architecture-and-lifecycle.md)
3. [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/scenarios/high-level-scenarios.md)
4. [examples/diagnostic-page-pattern.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/examples/diagnostic-page-pattern.md)
5. [examples/comprehensive-network-pattern.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/examples/comprehensive-network-pattern.md)
6. [references/probe-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/probe-reference-map.md)
7. [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/environment-variables.md)
8. [references/versioning-and-compatibility.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/versioning-and-compatibility.md)
9. [references/samples-validation.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/samples-validation.md)
10. [references/source-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/source-map.md)
11. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/troubleshooting/common-issues.md)
12. [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/RUNBOOK.md)

## Common Lifecycle Pattern

1. Initialize `Prober` / `Reporter`.
2. Request media permissions and enumerate devices.
3. Run targeted diagnostics (`diagnoseAudio`, `diagnoseVideo`).
4. Run comprehensive network diagnostic (`startToDiagnose`) and stream stats to UI.
5. Produce final report and apply readiness gates.
6. Stop/cleanup (`stopToDiagnose`, `stopToDiagnoseVideo`, `releaseMediaStream`, `cleanup`).

## High-Level Scenarios

- Pre-join diagnostics page before Meeting SDK join action.
- Support workflow that captures structured report for customer troubleshooting.
- Device certification flow for kiosk or controlled endpoint environments.
- Browser capability gating for advanced media features.

See [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/scenarios/high-level-scenarios.md) for details.

## Chaining

- Meeting pre-join gate: [../meeting-sdk/web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../meeting-sdk/web/SKILL.md)
- Video session readiness gate: [../video-sdk/web/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../video-sdk/web/SKILL.md)
- Telemetry/report ingestion backend: [../rivet-sdk/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../rivet-sdk/SKILL.md) + [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/../rest-api/SKILL.md)

## Environment Variables

- See [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/references/environment-variables.md) for optional `.env` keys and how to source values.

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/probe-sdk/RUNBOOK.md) - 5-minute preflight and debugging checklist.
