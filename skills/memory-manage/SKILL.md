---
name: memory-manage
description: Use when the user wants to inspect or edit the agent's long-term memory: 'what do you remember about me', 'recall my notes on X', 'remember that ...', 'save this to memory', 'forget that', 'remove that memory', or 'reset my memory'. Removing an entry or resetting memory is destructive and is confirmed first.
---

# Memory

Manage long-term memory via the `memory` tool.

- Recall / read: `memory(action="recall"|"read", ...)` (read-only).
- Save: `memory(action="save", ...)`.
- REMOVE (destructive): `memory(action="remove", ...)` deletes an entry.
  `hermes memory reset` erases everything. Confirm the exact target first; the
  system also gates removal via the approval card.
