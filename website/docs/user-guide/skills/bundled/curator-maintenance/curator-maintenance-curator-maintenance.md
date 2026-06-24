---
title: "Curator Maintenance"
sidebar_label: "Curator Maintenance"
description: "Use when the user wants to maintain or clean up their skill library: 'what is the curator doing', 'curator status', 'run the curator', 'pin this skill', 'unp..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Curator Maintenance

Use when the user wants to maintain or clean up their skill library: 'what is the curator doing', 'curator status', 'run the curator', 'pin this skill', 'unpin', 'archive unused skills', 'prune dead skills', 'restore an archived skill', or 'list archived skills'. Prune, archive, and a curator run change or remove skills and are confirmed first.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/curator-maintenance` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Curator maintenance

Manage the skill library via `hermes curator` (terminal) / curator tools.

- Status / list-archived: read-only, run autonomously.
- Pin / unpin: `hermes curator pin|unpin <skill>` (reversible).
- Restore: `hermes curator restore <skill>` (brings an archived skill back).
- ARCHIVE / PRUNE / RUN (destructive or consequential):
  `hermes curator archive <skill>`, `hermes curator prune`, `hermes curator run`.
  These remove/relocate skills or spend aux-model budget. Name what will change
  and confirm first; the system also gates these via the approval card.
- Pause / resume the curator: `hermes curator pause|resume` (low-risk).

## Backup / rollback (terminal)
- Take a manual snapshot: `hermes curator backup` (tar.gz of ~/.hermes/skills/,
  safe and reversible).
- ROLLBACK (destructive): `hermes curator rollback` restores ~/.hermes/skills/ from
  a snapshot, OVERWRITING the current skills. Name the snapshot and confirm first.
