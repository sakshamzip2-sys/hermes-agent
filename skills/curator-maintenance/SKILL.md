---
name: curator-maintenance
description: Use when the user wants to maintain or clean up their skill library: 'what is the curator doing', 'curator status', 'run the curator', 'pin this skill', 'unpin', 'archive unused skills', 'prune dead skills', 'restore an archived skill', or 'list archived skills'. Prune, archive, and a curator run change or remove skills and are confirmed first.
---

# Curator maintenance

Manage the skill library via `hermes curator` (terminal) / curator tools.

- Status / list-archived: read-only, run autonomously.
- Pin / unpin: `hermes curator pin|unpin <skill>` (reversible).
- Restore: `hermes curator restore <skill>` (brings an archived skill back).
- ARCHIVE / PRUNE / RUN (destructive or consequential):
  `hermes curator archive <skill>`, `hermes curator prune`, `hermes curator run`.
  These remove/relocate skills or spend aux-model budget. Name what will change
  and confirm first; the system also gates these via the approval card.
