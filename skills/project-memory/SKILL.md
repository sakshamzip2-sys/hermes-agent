---
name: project-memory
description: |
  Repo-level memory so a coding agent never rediscovers the same project twice. Enforces a
  dated, append-only convention IN the repo — docs/memory/YYYY-MM-DD/<slug>-memory-YYYY-MM-DD.md
  — that travels with the code (great for OSS and multi-day work). BEFORE work: read recent
  memory. AFTER meaningful work: write what changed, what was learned, decisions, open
  decisions, files touched, source docs, verification, constraints/gotchas, next steps. Small,
  dated, searchable — NOT transcript dumps. Use when a repo has recurring decisions, the agent
  keeps re-asking answered questions, or setup gotchas matter. Triggers: "remember this for the
  repo", "project memory", "log what we did", "write a memory entry", "save repo context",
  "what did we decide last time", "update project memory".
version: 1.0.0
metadata:
  opencomputer:
    tags: [memory, repo, continuity, docs, decisions]
    related_skills: [reset-safe-memory, memory-manage, llm-wiki]
---

# Project Memory

Repo memory lives in-repo (so it's portable and reviewable in git). Distinct from
`reset-safe-memory` (operator/session layer) and `memory-manage` (the long-term memory tool):
this one is about THE CODEBASE.

Convention (exact): `docs/memory/YYYY-MM-DD/<descriptive-slug>-memory-YYYY-MM-DD.md`

## Before work

Read the most recent entries under `docs/memory/` (newest dates first) before starting
non-trivial work. If the question you're about to ask was answered there, use the answer.

## After meaningful work

Create a new dated entry (one helper does the path + scaffold):

```bash
python3 scripts/new_memory.py "<short-slug>"        # writes docs/memory/<today>/<slug>-memory-<today>.md
python3 scripts/new_memory.py "fix-auth-redirect" --dir path/to/repo
```

Fill these exact sections (the template enforces them):

- **What changed** — the concrete edits/outcome.
- **What was learned** — non-obvious facts about this repo.
- **Decisions made** — with one-line rationale each.
- **Open decisions** — things still undecided (so the next run doesn't assume).
- **Files touched** — paths.
- **Source docs referenced** — where the truth came from.
- **Verification** — what you ran and the result (evidence, not "should work").
- **Constraints / gotchas** — traps the next run must avoid.
- **Next steps** — the obvious continuation.

## Rules / pitfalls

- **High-signal, not a transcript.** Bullets. If an entry is longer than a screen, it's
  probably dumping rather than curating.
- **Append-only / dated** — never rewrite history; add a new entry. Old entries are the trail.
- **In-repo, committed** — the memory must travel with the code (commit it alongside the work).
- Don't duplicate what git history/CLAUDE.md already records — capture what was *non-obvious*.

## Success criteria

A small dated entry under `docs/memory/` that lets the next session understand *why* something
exists and continue without re-deriving it — committed with the change it documents.
