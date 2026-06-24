---
title: "Kw Partner Built Zoom Meeting Sdk Electron — Zoom Meeting SDK for Electron desktop applications"
sidebar_label: "Kw Partner Built Zoom Meeting Sdk Electron"
description: "Zoom Meeting SDK for Electron desktop applications"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Zoom Meeting Sdk Electron

Zoom Meeting SDK for Electron desktop applications. Use when embedding Zoom meetings in an Electron app
with the Node addon wrapper, JWT auth, join/start flows, settings controllers, and raw data integration.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Meeting SDK (Electron)

Use this skill when building Electron desktop apps that embed Zoom Meeting SDK capabilities through the Electron wrapper.

## Start Here

1. **[Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/lifecycle-workflow.md)** - init -> auth -> join/start -> in-meeting -> cleanup
2. **[SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/sdk-architecture-pattern.md)** - service/controller/event model in Electron
3. **[Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/setup-guide.md)** - dependency and build expectations
4. **[Authentication Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/authentication-pattern.md)** - SDK JWT generation and auth callbacks
5. **[Join Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/join-meeting-pattern.md)** - start/join meeting execution flow
6. **[SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/SKILL.md)** - full navigation

## Core Notes

- Electron wrapper is built on top of native Meeting SDK with Node addon bridges.
- Keep SDK key/secret server-side; generate SDK JWT on backend.
- Feature support differs by platform/version; check module docs before implementation.
- Raw data and IPC patterns require explicit security hardening in production.

## References

- [Electron API Reference Index](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/references/electron-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/references/module-map.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/troubleshooting/deprecated-and-contradictions.md)

## Related Skills

- [zoom-meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/../SKILL.md)
- [zoom-oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/../../oauth/SKILL.md)
- [zoom-general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/../../general/SKILL.md)


## Merged from meeting-sdk/electron/SKILL.md

# Zoom Meeting SDK Electron - Documentation Index

## Start Here

1. [SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/SKILL.md)
2. [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/lifecycle-workflow.md)
3. [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/sdk-architecture-pattern.md)
4. [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/setup-guide.md)

## Concepts

- [Lifecycle Workflow](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/lifecycle-workflow.md)
- [SDK Architecture Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/sdk-architecture-pattern.md)
- [High-Level Scenarios](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/concepts/high-level-scenarios.md)

## Examples

- [Setup Guide](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/setup-guide.md)
- [Authentication Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/authentication-pattern.md)
- [Join Meeting Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/join-meeting-pattern.md)
- [Raw Data Pattern](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/examples/raw-data-pattern.md)

## References

- [Electron API Reference](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/references/electron-reference.md)
- [Module Map](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/references/module-map.md)

## Troubleshooting

- [Common Issues](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/troubleshooting/common-issues.md)
- [Version Drift](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/troubleshooting/version-drift.md)
- [Deprecated and Contradictions](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/troubleshooting/deprecated-and-contradictions.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/meeting-sdk/electron/RUNBOOK.md) - 5-minute preflight and debugging checklist.
