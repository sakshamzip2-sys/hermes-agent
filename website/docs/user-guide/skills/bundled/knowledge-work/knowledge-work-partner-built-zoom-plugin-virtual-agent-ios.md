---
title: "Kw Partner Built Virtual Agent Ios — Zoom Virtual Agent iOS integration via WKWebView"
sidebar_label: "Kw Partner Built Virtual Agent Ios"
description: "Zoom Virtual Agent iOS integration via WKWebView"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Virtual Agent Ios

Zoom Virtual Agent iOS integration via WKWebView. Use for Swift/Objective-C script injection, message handlers, support_handoff relay, and URL routing policies.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Virtual Agent - iOS

Official docs:
- https://developers.zoom.us/docs/virtual-agent/ios/

## Quick Links

1. [concepts/webview-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/concepts/webview-lifecycle.md)
2. [examples/js-bridge-patterns.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/examples/js-bridge-patterns.md)
3. [references/ios-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/references/ios-reference-map.md)
4. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/troubleshooting/common-issues.md)

## Integration Model

- Load campaign URL in `WKWebView`.
- Inject `window.zoomCampaignSdkConfig` using `WKUserScript`.
- Register message handlers for exit/common/handoff flows.
- Handle URL behavior in navigation delegates (`in-app`, `SFSafariViewController`, or system browser).

## Hard Guardrails

- Register scripts and handlers before web interaction.
- Handle iOS 14.5+ download behavior where needed.
- Keep deprecated `openURL` command support as fallback only.

## Chaining

- Product-level patterns: [../SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/../SKILL.md)
- Contact Center mobile scope: [../../contact-center/ios/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/ios/../../contact-center/ios/SKILL.md)
