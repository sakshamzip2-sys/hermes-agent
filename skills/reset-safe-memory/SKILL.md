---
name: reset-safe-memory
description: |
  Survive context resets and session handoffs without losing critical context. Provides
  prepforreset (write a daily + session log BEFORE any reset/handoff), session-handoff
  (summarize key context so the next session starts clean), and wikijanitor (conservative
  review of the memory wiki — stale notes, duplicates, gaps, contradictions — emitted as a
  report that PROPOSES, never auto-deletes). Curated, decision-focused, human-readable memory
  — not transcript dumps. Use when: a context reset/compaction is near, a long session is
  ending, you switch sessions/connectors (Telegram, gateway, cron), or the wiki needs cleanup.
  Triggers: "prep for reset", "prepforreset", "I'm about to reset", "session handoff",
  "save context before reset", "wikijanitor", "clean up the memory wiki", "before you forget".
version: 1.0.0
metadata:
  opencomputer:
    tags: [memory, reset, handoff, continuity, wiki, curation]
    related_skills: [llm-wiki, memory-manage, project-memory]
---

# Reset-Safe Memory

Operator-layer memory that endures across resets. The vault default is
`~/.hermes/memory-wiki/`; override with `MEMORY_WIKI_DIR`. Structure:

```
memory-wiki/
  daily-logs/        YYYY-MM-DD.md            (one per day, appended)
  session-logs/      YYYY-MM-DD-<slug>.md     (one per session)
  prepforreset/      latest.md                (the handoff the next session reads FIRST)
  decisions/         <topic>.md               (durable decisions + rationale + alternatives)
  open-loops/        index.md                 (unfinished threads, routing candidates)
  wikijanitor-reports/ YYYY-MM-DD.md
```

Write boundaries: only ever write under the vault dir. Use `[[wikilinks]]` heavily so
knowledge compounds.

## prepforreset (MANDATORY before any reset / session end / long handoff)

Run this routine when a reset is near OR the user signals the session is wrapping:

1. **Daily log** — append today's `daily-logs/YYYY-MM-DD.md`: what happened, decisions, artifacts.
2. **Session log** — write `session-logs/YYYY-MM-DD-<slug>.md`: key events, decisions made,
   artifacts created/paths, and OPEN items.
3. **Handoff** — overwrite `prepforreset/latest.md` using `templates/prepforreset.template.md`:
   a tight "context summary for the next session" + pending actions + escalations. This is the
   ONE file the next session must read first.

Memory is **curated, not dumped**: decisions, rationale, alternatives, artifacts, open loops,
source refs, gotchas — in bullets. Never paste raw transcript.

## session-handoff (start of a new session)

On a fresh session (or right after a reset/compaction): read `prepforreset/latest.md` and the
most recent `session-logs/*`, then state a 3–5 bullet "where we left off" before acting.

## wikijanitor (conservative cleanup)

```bash
python3 scripts/wikijanitor.py [vault-dir]     # default: $MEMORY_WIKI_DIR or ~/.hermes/memory-wiki
```

It scans the vault and writes `wikijanitor-reports/YYYY-MM-DD.md` listing: **stale** notes
(old mtime), **review candidates** (very short / TODO-only), **possible duplicates** (similar
titles), **gaps** (broken `[[wikilinks]]` with no target file), and **contradictions to check**.
It only PROPOSES — never deletes or edits. You read the report and decide.

## Rules / pitfalls

- prepforreset is not optional — a reset without it loses the thread. Run it on the FIRST sign
  of wrap-up or compaction, not after.
- Janitor is conservative by design: no auto-delete, no auto-merge. Surface, then let a human
  (or an explicit follow-up) decide.
- Keep the handoff small and high-signal — if the next session can't orient from
  `prepforreset/latest.md` in 30 seconds, it's too long.

## Success criteria

After a reset, a new session reads `prepforreset/latest.md` + recent session log and continues
the work without re-asking answered questions; the janitor report flags real cruft without
destroying anything.
