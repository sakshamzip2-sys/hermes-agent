---
name: profiles-manage
description: Use when the user wants to work with agent profiles/personas: 'list my profiles', 'what profiles do I have', 'switch to the finance profile', 'create a new profile', or 'delete that profile'. Deleting a profile wipes its home directory and is destructive, so it is confirmed first.
---

# Profiles

Manage agent profiles via the `hermes profile` CLI (run through the terminal).

- List: `hermes profile list` (read-only).
- Create: `hermes profile create <name>`.
- DELETE (destructive): `hermes profile delete <name>` wipes the profile home.
  Name the exact profile and confirm before running; it is irreversible.

Report which profile changed.
