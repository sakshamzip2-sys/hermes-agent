---
title: "Kw Partner Built Setup Zoom Oauth — Implement Zoom authentication correctly"
sidebar_label: "Kw Partner Built Setup Zoom Oauth"
description: "Implement Zoom authentication correctly"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Partner Built Setup Zoom Oauth

Implement Zoom authentication correctly. Use when setting up app credentials, choosing an OAuth grant, requesting scopes, handling token refresh, or debugging auth failures.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-oauth` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /setup-zoom-oauth

Use this skill when auth is the blocker or when auth choices will shape the entire integration.

## Scope

- App type selection
- OAuth grant selection
- Scope planning
- Token exchange and refresh
- Auth debugging and environment assumptions

## Workflow

1. Determine the app model and who is authorizing whom.
2. Choose the correct grant flow.
3. Identify minimum scopes for the user flow.
4. Define token storage and refresh behavior.
5. Route into the deepest relevant reference docs only after the above is clear.

## Primary References

- [oauth](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-oauth/../oauth/SKILL.md)
- [general](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-oauth/../general/SKILL.md)
- [rest-api](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/partner-built/zoom-plugin/skills/setup-zoom-oauth/../rest-api/SKILL.md)

## Common Mistakes

- Picking a grant before clarifying the actor and tenant model
- Asking for broad scopes before confirming the exact workflow
- Forgetting refresh-token behavior and token lifecycle handling
- Reusing an old refresh token after a successful refresh instead of storing the newly returned one
- Treating auth failures as API failures without checking app configuration first
