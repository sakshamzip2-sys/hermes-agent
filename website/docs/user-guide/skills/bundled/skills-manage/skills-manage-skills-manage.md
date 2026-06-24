---
title: "Skills Manage"
sidebar_label: "Skills Manage"
description: "Use when the user wants to inspect or manage an INDIVIDUAL skill (not run it, and not bundles): 'what skills do I have', 'list my skills', 'show me the xlsx ..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Skills Manage

Use when the user wants to inspect or manage an INDIVIDUAL skill (not run it, and not bundles): 'what skills do I have', 'list my skills', 'show me the xlsx skill', 'open the X skill', 'view a skill', 'create a new skill', 'edit this skill', or 'delete a skill'. Deleting a skill or its files is destructive and is confirmed first.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/skills-manage` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Skills management

Manage individual skills via `skills_list` / `skill_view` / `skill_manage`.

- List / view: `skills_list`, `skill_view(name=...)` (read-only).
- Create / edit: `skill_manage(action="create"|"patch"|"edit", ...)`.
- DELETE (destructive): `skill_manage(action="delete"|"remove_file", ...)`
  removes a skill or its files. Confirm the exact skill first; the system also
  gates deletion via the approval card.

## Skills hub (terminal: `hermes skills ...`)
- Browse / search the registries: `hermes skills browse`, `hermes skills search <q>`
  (read-only).
- List installed / list your edits: `hermes skills list`, `hermes skills list-modified`.
- Audit installed hub skills: `hermes skills audit` (read-only re-scan).
- Snapshot config: `hermes skills snapshot export|import` (export/import skill setup).
- INSTALL a skill from the hub: `hermes skills install <name>`.
- UNINSTALL a hub skill (destructive): `hermes skills uninstall <name>`. Confirm first.
- Restore official optional skills: `hermes skills repair-official` (use `--restore`
  to overwrite local edits; confirm because it can clobber your changes).
