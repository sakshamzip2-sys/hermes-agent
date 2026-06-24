---
title: "Kw Partner Built Virtual Agent Android — Zoom Virtual Agent Android integration via WebView"
sidebar_label: "Kw Partner Built Virtual Agent Android"
description: "Zoom Virtual Agent Android integration via WebView"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Virtual Agent Android

Zoom Virtual Agent Android integration via WebView. Use for Java/Kotlin bridge callbacks, native URL handling, support_handoff relay, and lifecycle-safe embedding.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom Virtual Agent - Android

Official docs:
- https://developers.zoom.us/docs/virtual-agent/android/

## Quick Links

1. [concepts/webview-lifecycle.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/concepts/webview-lifecycle.md)
2. [examples/js-bridge-patterns.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/examples/js-bridge-patterns.md)
3. [references/android-reference-map.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/references/android-reference-map.md)
4. [troubleshooting/common-issues.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/troubleshooting/common-issues.md)

## Integration Model

- Host campaign URL in Android WebView.
- Inject runtime context (`window.zoomCampaignSdkConfig`).
- Register JavaScript bridge for `exitHandler`, `commonHandler`, `support_handoff`.
- Apply URL policy via `shouldOverrideUrlLoading` and optional multi-window callbacks.

## Hard Guardrails

- Initialize handlers before expecting JS callbacks.
- Treat legacy `openURL` command handling as compatibility path only.
- Prefer DOM links or `window.open` handling plus explicit native routing.

## Chaining

- Product-level patterns: [../SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/../SKILL.md)
- Contact Center mobile scope: [../../contact-center/android/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/virtual-agent/android/../../contact-center/android/SKILL.md)
