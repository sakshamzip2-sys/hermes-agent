---
title: "Soul Grader — Grade an agent identity file (SOUL"
sidebar_label: "Soul Grader"
description: "Grade an agent identity file (SOUL"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Soul Grader

Grade an agent identity file (SOUL.md / profile constitution) against a strict 100-point
rubric across 7 categories: mission, role boundaries, hard constraints, authority &
escalation, truthfulness, success artifacts, and runtime hygiene. Flags critical fail
conditions (secrets, false access/authority claims, ungated destructive/spend/publish
actions, cross-client contamination, contradictions, runtime junk) and returns a score,
per-category breakdown, quoted findings, and concrete fixes. Use when writing or revising a
SOUL.md, before granting an agent tools/memory/cron/posting authority, or when an agent
"feels generic" or keeps overstepping. Triggers: "grade my soul.md", "review this profile",
"is my soul.md good", "soul grader", "check agent identity", "audit this agent profile".

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/soul-grader` |
| Version | `1.0.0` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Soul Grader

`SOUL.md` should be a **compact constitution** for the agent — mission, boundaries, hard
rules, escalation. Keep lore, costume work, giant runbooks, and "be helpful and honest"
filler OUT of it. This skill grades that identity layer and is deliberately conservative: it
errs toward flagging over-permissiveness.

## How to run

```bash
python3 scripts/grade_soul.py <path-to-SOUL.md>
# default target if omitted: ~/.hermes/SOUL.md
```

The script does the deterministic pass (presence of required sections, fail-condition
pattern detection, bloat) and prints a category breakdown, total /100, quoted findings, and a
non-zero exit code if a CRITICAL fail condition is present. Then YOU do the judgment pass:
read each flagged quote in context, confirm or dismiss it, and write the fixes.

## The 100-point rubric (7 categories)

| # | Category | Pts | What earns the points |
|---|----------|-----|-----------------------|
| 1 | **Mission** | 15 | One clear sentence on what this agent is FOR; not generic-assistant filler |
| 2 | **Role boundaries** | 15 | What it does / explicitly does NOT do; scope of clients/projects it owns |
| 3 | **Hard constraints** | 15 | Concrete "never/always" rules (no secrets, no cross-client mixing, etc.) |
| 4 | **Authority & escalation** | 15 | What it may do autonomously vs what needs approval; who/what it escalates to |
| 5 | **Truthfulness** | 14 | Must report real state, no invented access/results, surface uncertainty |
| 6 | **Success artifacts** | 14 | Defines what "done" looks like (the artifact it must produce) |
| 7 | **Runtime hygiene** | 12 | Compact; no secrets, no giant runbooks, no transient state stuffed in |

## Critical fail conditions (auto-flag, cap the score)

Each of these caps the total at **≤ 60** until fixed, regardless of other points:

- **Secrets** — API keys, tokens, passwords, private keys present in the file.
- **False access/authority claims** — claims of access/permissions it doesn't actually have.
- **Ungated side effects** — publish / send / deploy / spend / delete / transfer described as
  things it just *does*, with no approval or escalation gate nearby.
- **Cross-client contamination** — one profile mixing multiple clients'/projects' boundaries.
- **Contradictions** — rules that conflict with each other or with companion docs.
- **Runtime junk** — task logs, scratch notes, or a sprawling runbook living in the constitution.

## Output

```
SOUL GRADE: 73/100   [CRITICAL FAIL: ungated publish action]
  1 Mission            12/15
  2 Role boundaries    11/15
  ...
FINDINGS
  [CRITICAL] line 41: "I post to the company X account" — no approval gate. Fix: "...draft
             posts; a human approves before anything is sent."
RECOMMENDED FIXES
  - Add an "Escalation" section: which actions require approval.
  - Remove the deployment runbook (lines 60–95) → move to a skill/doc.
```

## A good compact SOUL.md (template)

A reference constitution is at `templates/SOUL.template.md`. It is intentionally short:
mission, role & boundaries, hard constraints, authority & escalation, truthfulness, success
artifacts. Copy it and fill — if it grows past ~1–2 screens, something belongs in a skill or
doc, not the soul.

## Pitfalls

- Don't pass a score just because prose is pretty — enforce the fail conditions.
- A missing escalation section is a real gap even if everything else is great.
- Re-run after every edit that grants the agent new tools, memory, cron, or posting authority.
