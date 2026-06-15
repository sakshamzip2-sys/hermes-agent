---
name: silent-failure-hunter
description: "Audit code for swallowed errors and silent failures."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  opencomputer:
    tags: [code-review, error-handling, debugging, reliability, audit]
    related_skills: [systematic-debugging, github-code-review]
---

<!-- Ported from OpenComputer v1 (originally everything-claude-code, MIT). -->

# Silent Failure Hunter

Audit a module or diff specifically for hidden failure modes — swallowed errors, bad
fallbacks, and missing error propagation. Have zero tolerance for silent failures: a
failure that never surfaces is harder to diagnose than a loud crash. Use `read_file`
and `search_files` to inspect the target code; this skill produces findings, not patches.

## When to Use

- Reviewing a PR or module for reliability before merge.
- A production bug "came from nowhere" and you suspect a swallowed error.
- Auditing code that touches I/O boundaries (network, disk, db, queues, subprocess).

## Hunt Targets

**1. Empty catch blocks** — `except Exception: pass`, `catch (_) {}`, errors turned
into `null`/`[]` with no context, Go `_ = err`.

**2. Inadequate logging** — logs missing context (request/user id, inputs), errors
logged at `info`/`debug`, log-and-return-success-shaped-result.

**3. Dangerous fallbacks** — defaults that hide failure (`return [] on any error`),
`.catch(() => [])`, "best effort" handlers that never surface to a metric or alert.

**4. Error propagation issues** — lost stack traces (raising without `from e` /
throwing without `cause`), generic rethrows that strip type info, unawaited promises /
fire-and-forget tasks with no error path, wrapping that loses the inner message.

**5. Missing error handling** — no timeout on network/file/db calls, no error path
around external integrations, no rollback around transactional work, assumed-success
on operations that can fail (file writes, lock acquisition, queue publish).

## How to Run the Audit

1. Identify the entry points and I/O boundaries of the target code.
2. For each I/O call, trace what happens on the failure path.
3. For each `try`/`catch`, ask: "what does the caller see if this catch fires?"
4. For each fallback default, ask: "would the caller behave differently if this had
   thrown?"
5. For each async/concurrent call, confirm the error path is awaited and surfaced.

## Output Format

For each finding: **location** (file:line) · **severity**
(`critical`|`high`|`medium`|`low`) · **issue** (one sentence) · **impact** (what goes
wrong in production, concretely) · **fix recommendation** (the specific 2–5 line change).

Severity calibration: **critical** = data loss / security / silent corruption;
**high** = user-visible bug that's hard to diagnose; **medium** = noisy on-call /
slower debugging; **low** = hygiene that would catch a future bug.

## Pitfalls

- Don't flag legitimate, documented `try`/`catch` where the catch IS the contract
  (cache miss, optional file).
- Don't flag every informational `log` — focus on swallowed *errors*.
- Don't refactor — recommend fixes precisely, but let the human apply them.

## Verification

Re-read each finding and confirm the failure path genuinely loses information a caller
or operator needs — if the catch is part of the contract, drop the finding.
