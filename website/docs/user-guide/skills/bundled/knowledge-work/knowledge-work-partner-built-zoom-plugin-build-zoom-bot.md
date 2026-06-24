---
title: "Kw Partner Built Build Zoom Bot — Build a Zoom meeting bot, recorder, or real-time media workflow"
sidebar_label: "Kw Partner Built Build Zoom Bot"
description: "Build a Zoom meeting bot, recorder, or real-time media workflow"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Build Zoom Bot

Build a Zoom meeting bot, recorder, or real-time media workflow. Use when joining meetings programmatically, processing live media or transcripts, or combining Meeting SDK, RTMS, and backend services.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /build-zoom-bot

Use this skill for automation that joins meetings, captures media, or reacts to live session data.

## Covers

- Bot architecture
- Meeting join strategy
- Real-time media and transcript handling
- Backend orchestration
- Storage, post-processing, and event flow design

## Workflow

1. Clarify whether the bot needs to join, observe, transcribe, summarize, or act.
2. Route to Meeting SDK and RTMS as the core implementation path.
3. Add REST API for meeting/resource management and Webhooks for asynchronous events when needed.
4. Call out environment and lifecycle constraints early.

## Primary References

- [meeting-sdk](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot/../meeting-sdk/SKILL.md)
- [rtms](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot/../rtms/SKILL.md)
- [scribe](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot/../scribe/SKILL.md)
- [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot/../rest-api/SKILL.md)
- [webhooks](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/build-zoom-bot/../webhooks/SKILL.md)

## Common Mistakes

- Treating batch transcription and live media as the same workflow
- Designing the bot before defining join authority and auth model
- Forgetting post-meeting storage and retry behavior
