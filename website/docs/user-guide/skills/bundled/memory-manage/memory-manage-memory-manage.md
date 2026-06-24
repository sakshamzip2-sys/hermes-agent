---
title: "Memory Manage"
sidebar_label: "Memory Manage"
description: "Use when the user wants to inspect or edit the agent's long-term memory: 'what do you remember about me', 'recall my notes on X', 'remember that"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Memory Manage

Use when the user wants to inspect or edit the agent's long-term memory: 'what do you remember about me', 'recall my notes on X', 'remember that ...', 'save this to memory', 'forget that', 'remove that memory', or 'reset my memory'. Removing an entry or resetting memory is destructive and is confirmed first.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/memory-manage` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Memory

Manage long-term memory via the `memory` tool.

- Recall / read: `memory(action="recall"|"read", ...)` (read-only).
- Save: `memory(action="save", ...)`.
- REMOVE (destructive): `memory(action="remove", ...)` deletes an entry.
  `hermes memory reset` erases everything (MEMORY.md + USER.md). Confirm the exact
  target first; the system also gates removal via the approval card.

## Provider configuration (terminal)
- Show the current provider config: `hermes memory status` (read-only).
- Configure / switch the memory provider: `hermes memory setup` (interactive
  selection across the supported providers, e.g. honcho and others). This changes
  where long-term memory is stored, so confirm before switching.
