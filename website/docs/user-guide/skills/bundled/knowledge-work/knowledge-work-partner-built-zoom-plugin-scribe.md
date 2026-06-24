---
title: "Kw Partner Built Scribe — Reference skill for Zoom AI Services Scribe"
sidebar_label: "Kw Partner Built Scribe"
description: "Reference skill for Zoom AI Services Scribe"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Scribe

Reference skill for Zoom AI Services Scribe. Use after routing to a transcription workflow when handling uploaded or stored media, Build-platform JWT auth, fast mode transcription, batch jobs, or transcript pipeline design.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/scribe` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Zoom AI Services Scribe

Background reference for Zoom AI Services Scribe across:
- synchronous single-file transcription (`POST /aiservices/scribe/transcribe`)
- asynchronous batch jobs (`/aiservices/scribe/jobs*`)
- browser microphone pseudo-streaming via repeated short file uploads
- webhook-driven batch status updates
- Build-platform JWT generation and credential handling

Official docs:
- https://developers.zoom.us/docs/ai-services/
- https://developers.zoom.us/docs/ai-services/scribe/
- https://developers.zoom.us/docs/api/ai-services/
- https://developers.zoom.us/api-hub/ai-services/methods/endpoints.json
- Quickstart sample: https://github.com/zoom/scribe-quickstart/

## Routing Guardrail

- If the user needs **uploaded or stored media transcribed into text**, route here first.
- If the user needs **live meeting media** without file-based upload/batch jobs, route to [../rtms/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../rtms/SKILL.md).
- If the user needs **Zoom REST API inventory** for AI Services paths, chain [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../rest-api/SKILL.md).
- If the user needs webhook signature patterns or generic HMAC receiver hardening, optionally chain [../webhooks/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../webhooks/SKILL.md).

## Quick Links

1. [concepts/auth-and-processing-modes.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/concepts/auth-and-processing-modes.md)
2. [scenarios/high-level-scenarios.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/scenarios/high-level-scenarios.md)
3. [examples/fast-mode-node.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/examples/fast-mode-node.md)
4. [examples/batch-webhook-pipeline.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/examples/batch-webhook-pipeline.md)
5. [references/api-reference.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/references/api-reference.md)
6. [references/environment-variables.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/references/environment-variables.md)
7. [references/samples-validation.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/references/samples-validation.md)
8. [references/versioning-and-drift.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/references/versioning-and-drift.md)
9. [troubleshooting/common-drift-and-breaks.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/troubleshooting/common-drift-and-breaks.md)
10. [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/RUNBOOK.md)

## Core Workflow

1. Get Build-platform credentials and generate an HS256 JWT.
2. Choose **fast mode** for one short file or **batch mode** for stored archives / large sets.
3. Submit the transcription request.
4. For batch jobs, poll job/file status or receive webhook notifications.
5. Persist and post-process transcript JSON.

## Hosted Fast-Mode Guardrail

- The formal fast-mode API limits are `100 MB` and `2 hours`, but hosted browser flows can still time out before the upstream response returns.
- Current deployed-sample observations:
  - ~17.2 MB MP4 completed in about `26s`
  - ~38.6 MB MP4 completed in about `26-37s`
  - ~59.2 MB MP4 completed in about `32-34s` on the backend
  - some ~59.2 MB browser requests still surfaced as frontend `504` while backend logs later showed `200`
- Treat frontend `504` plus backend `200` as a browser/edge timeout race, not an automatic transcription failure.
- For hosted UIs, prefer an async request/polling wrapper for fast mode instead of holding the browser open for the full upstream response.
- For larger or less predictable media, prefer batch mode even when the file is still within the formal fast-mode size limit.

## Browser Microphone Pattern

- `scribe` does not expose a documented real-time streaming API surface.
- If you want a browser microphone experience, use pseudo-streaming:
  1. capture microphone audio in short chunks
  2. upload each chunk through the async fast-mode wrapper
  3. poll for completion
  4. append chunk transcripts in sequence
- Recommended starting cadence:
  - chunk size: `5 seconds`
  - acceptable range: `5-10 seconds`
  - in-flight chunk requests: `2-3`
- This is a practical UI pattern for incremental transcript updates, not a substitute for `rtms`.
- Treat this as a fallback demo pattern, not the preferred production architecture.
- It adds repeated upload overhead, chunk-boundary drift, browser codec/container variability, and transcript stitching complexity.
- If the user asks for actual live stream ingestion, low-latency continuous media, or server-push media transport, route to [../rtms/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../rtms/SKILL.md) instead.

## Endpoint Surface

| Mode | Method | Path | Use |
|------|--------|------|-----|
| Fast | `POST` | `/aiservices/scribe/transcribe` | Synchronous transcription for one file |
| Batch | `POST` | `/aiservices/scribe/jobs` | Submit asynchronous batch job |
| Batch | `GET` | `/aiservices/scribe/jobs` | List jobs |
| Batch | `GET` | `/aiservices/scribe/jobs/{jobId}` | Inspect job summary/state |
| Batch | `DELETE` | `/aiservices/scribe/jobs/{jobId}` | Cancel queued/processing job |
| Batch | `GET` | `/aiservices/scribe/jobs/{jobId}/files` | Inspect per-file results |

## High-Level Scenarios

- On-demand clip transcription after a user uploads one recording.
- Batch transcription of stored S3 call archives.
- Webhook-driven ETL pipeline that writes transcripts to your database/search index.
- Re-transcription of Zoom-managed recordings after exporting them to your own storage.
- Offline compliance or QA workflows that need timestamps, channel separation, and speaker hints.

## Chaining

- Stored Zoom recordings -> [../rest-api/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../rest-api/SKILL.md) + `scribe`
- Webhook verification hardening -> [../webhooks/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../webhooks/SKILL.md)
- Real-time live transcript/media -> [../rtms/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../rtms/SKILL.md)
- Cross-product routing -> [../general/SKILL.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/../general/SKILL.md)

## Operations

- [RUNBOOK.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/scribe/RUNBOOK.md) - 5-minute preflight and debugging checklist.
