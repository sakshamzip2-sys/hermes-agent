---
name: profiles-manage
destructive: true
description: Use when the user wants to work with agent profiles/personas: 'list my profiles', 'what profiles do I have', 'switch to the finance profile', 'create a new profile', or 'delete that profile'. Deleting a profile wipes its home directory and is destructive, so it is confirmed first.
---

# Profiles

Manage agent profiles via the `hermes profile` CLI (run through the terminal).
Each profile is an isolated agent home (its own SOUL, memory, sessions, skills).

## Read-only (run autonomously)
- List: `hermes profile list`.
- Show details: `hermes profile show <name>`.
- Distribution manifest: `hermes profile info <name>`.
- Read the routing description: `hermes profile describe <name>`.

## Create / clone
- New: `hermes profile create <name>`.
- Clone an existing profile's identity + skills: `hermes profile create <name> --clone-from <source>`
  (`--clone` copies config/.env/SOUL/skills; `--clone-all` copies all state).

## Switch / label (low-risk)
- Set the sticky default: `hermes profile use <name>`.
- Set the description: `hermes profile describe <name> "<text>"`.
- Manage wrapper scripts (aliases): `hermes profile alias ...`.

## Distribution
- Install from a git URL or local path: `hermes profile install <source>`.
- Re-pull and apply updates: `hermes profile update <name>`.
- Export to an archive: `hermes profile export <name>`.
- Import from an archive: `hermes profile import <archive>`.

## Destructive (confirm first, then run; the approval card also fires)
- DELETE: `hermes profile delete <name>` wipes the profile home, irreversible.
- RENAME: `hermes profile rename <old> <new>` (can break existing aliases/sessions
  that reference the old name). Confirm the exact names first.

Report which profile changed.
