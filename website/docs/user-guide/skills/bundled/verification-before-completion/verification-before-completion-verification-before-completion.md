---
title: "Verification Before Completion — Run verification before claiming work is done"
sidebar_label: "Verification Before Completion"
description: "Run verification before claiming work is done"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Verification Before Completion

Run verification before claiming work is done.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/verification-before-completion` |
| Version | `1.0.0` |
| Platforms | linux, macos, windows |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

<!-- Ported from OpenComputer v1 (originally claude-plugins-official/superpowers, MIT). -->

# Verification Before Completion

Claiming work is complete without verification is dishonesty, not efficiency. The
core principle is **evidence before claims, always**. Use this before any statement
that work is done, fixed, or passing — especially before committing or opening a PR.

## When to Use

- About to say a task is complete / fixed / passing.
- About to commit, push, or open a PR.
- About to move to the next task or trust a subagent's "success" report.
- Any expression of satisfaction about the work state ("Great!", "Done!").

## The Iron Law

**No completion claim without fresh verification evidence.** If you have not run the
verifying command in this turn, you cannot claim it passes.

## The Gate (run before any status claim)

1. **Identify** — what command proves this claim?
2. **Run** — execute the FULL command fresh (use `terminal`).
3. **Read** — full output, check the exit code, count failures.
4. **Verify** — does the output actually confirm the claim? If not, state the real
   status with evidence.
5. **Only then** — make the claim, with the evidence.

Skipping any step is claiming, not verifying.

## Quick Reference

| Claim | Requires | NOT sufficient |
|-------|----------|----------------|
| Tests pass | Test output: 0 failures | "should pass", a previous run |
| Linter clean | Linter output: 0 errors | a partial check |
| Build succeeds | Build command: exit 0 | "linter passed" |
| Bug fixed | Original symptom now passes | code changed, assumed fixed |
| Regression test works | Red→green cycle verified | test passes once |
| Subagent done | VCS diff shows real changes | the agent said "success" |
| Requirements met | Line-by-line checklist | "tests pass" |

## Red Flags — STOP

- Using "should", "probably", "seems to".
- Expressing satisfaction before running verification.
- About to commit/PR without verification.
- Trusting a subagent's success report without checking the diff.
- "Just this once" / "I'm confident" / "I'm tired".

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should work now" | Run the verification. |
| "I'm confident" | Confidence ≠ evidence. |
| "Linter passed" | Linter ≠ compiler. |
| "Agent said success" | Verify independently (check the diff). |
| "Partial check is enough" | Partial proves nothing. |

## Procedure

1. Before any completion language, list the claims you are about to make.
2. For each claim, run its verifying command with `terminal` and read the full output.
3. For a regression test, verify the red→green cycle: write the test, run (fail
   before fix / pass after), revert the fix and confirm it fails, restore and confirm
   it passes.
4. For requirements, re-read the spec, build a checklist, verify each item, and report
   gaps honestly rather than rounding up to "done".

## Pitfalls

- Verifying a synonym of the claim, not the claim itself ("the build linted" ≠ "the
  build compiles").
- Reporting an old run's output as if it were fresh.
- Treating a subagent's narrative as evidence instead of inspecting its diff.

## Verification

The bottom line: run the command, read the output, THEN claim the result.
