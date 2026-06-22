---
name: swe-delegation
description: "Orchestrate a coding task as a router: Claude Code PLANS, Codex EXECUTES, Hermes delegates and verifies. The OpenComputerV2 agent runs this as the `coding` profile and owns the tmux terminal lifecycle (open / monitor / fork / end) and the Claude Code + Codex slash commands. Use when acting as the coding router/delegator."
version: 2.0.0
author: OpenComputer
license: MIT
platforms: [linux, macos, windows]
metadata:
  opencomputer:
    tags: [Coding-Agent, Orchestration, Delegation, Router, Planner, Executor, Claude, Codex, tmux]
    related_skills: [claude-code, codex, hermes-agent, opencode]
---

# SWE Delegation — Claude Code plans, Codex executes

You are the delegator. You do not write the code yourself. You route a coding task
between two specialist CLI agents and verify the result:

- **Claude Code = the PLANNER.** It reads the repo and produces an ordered plan.
- **Codex = the EXECUTOR.** It takes the plan and makes the change real.
- **You (Hermes) = the router.** You decompose, carry the plan from planner to
  executor, verify with real output, and loop back when a step is wrong.

The orchestrator is the OpenComputerV2 agent itself, running as the `coding`
profile: there is no separate process. You drive both CLIs over your `terminal`
and `process` tools, and you own the terminal lifecycle. This skill is the protocol;
read the `claude-code` and `codex` skills for the full flag and slash-command
reference.

## Prerequisites (check once; report any missing to the user)

- Planner: `claude --version` (v2.x) and `claude auth status`.
- Executor: `codex --version` and a working login (`codex login status`). Codex
  also REQUIRES a git repository.
- `tmux -V` for interactive sessions; install if absent.
- Install CLIs if absent: `npm install -g @anthropic-ai/claude-code @openai/codex`.
- Both CLIs are paid/metered and authenticated separately. Delegating to them is a
  spend, so treat a live run as a gated action and confirm scope before a big batch.

## Two execution modes

**Mode A — one-shot (print mode).** Fast, no PTY, structured output. Best for a
single plan or a scoped execution. Used in the loop below.

**Mode B — interactive over tmux.** For multi-turn iterative work (plan -> review ->
fix -> test in the same live session) and for a 24/7 host. YOU own the lifecycle:
open the session, send keys, monitor with capture-pane, and decide when to keep,
fork, or kill it. See "Terminal lifecycle" and the `claude-code` skill's tmux
section for the exact send-keys patterns.

## The delegation loop

### Step 1 — Decompose and route (you)
Restate the task in one line. Decide the route: a small, well-understood change can
go straight to the executor; anything non-trivial gets a planning pass first. Pin
the repo path (`workdir`) and the constraints (tests to keep green, files in scope).

### Step 2 — Delegate PLANNING to Claude Code
Run the planner read-only so it CANNOT edit, and tell it to emit the whole plan as
its final message. (Do not use `--permission-mode plan` for capture: in print mode
that returns only a short "plan ready for approval" line, not the plan itself.)

```
terminal(command="claude -p 'Read this repo and output the COMPLETE implementation plan as your final message for: <TASK>. List the exact files to change, the full change in each, and how to verify. Do NOT edit any files.' --allowedTools 'Read Glob Grep' --output-format json --max-turns 8", workdir="<repo>", timeout=180)
```

Parse `result` from the JSON; that text is THE PLAN. This print-mode call needs no
PTY (it is one-shot and exits). If the plan is vague, send a refining prompt before
moving on. For a live multi-turn planning session, switch to the tmux interactive
mode above and use `/plan` (Shift+Tab to plan mode), `/context`, and `ultrathink`.

The user grows the planner over time via the repo's `CLAUDE.md`, `.claude/rules/*.md`,
and `.claude/agents/*` — that is where Claude Code is configured into a better
planner, separately from this skill.

### Step 3 — Delegate EXECUTION to Codex
Hand the captured plan to Codex as the task. Codex writes code and runs commands in
the workspace. Use `pty=true` (Codex is an interactive TUI) and background long work:

```
terminal(command="codex exec --full-auto -m <supported-model> 'Implement exactly this plan, no more: <PASTE THE PLAN FROM STEP 2>. Run the project tests when done and report the result.'", workdir="<repo>", pty=true, background=true)
# pty=true is required: Codex is an interactive TUI and hangs without a PTY.
process(action="poll", session_id="<id>")   # monitor
process(action="log",  session_id="<id>")
process(action="submit", session_id="<id>", data="yes")   # if it asks a question
```

Model note (real gotcha): on a ChatGPT login, Codex rejects models its plan does not
include with `"<model> is not supported when using Codex with a ChatGPT account"`.
If you hit this, pass a supported `-m <model>`, or set `model` in
`~/.codex/config.toml`, or have the user re-run `codex login` / set `OPENAI_API_KEY`.
In a gateway/service context where bubblewrap sandboxing fails, fall back to
`codex exec --sandbox danger-full-access "<task>"` and rely on process boundaries
(clean git status, narrow prompt, diff review) as the safety layer.

The user grows the executor via Codex's own `AGENTS.md` and `~/.codex/config.toml`,
separately from this skill.

### Step 4 — Verify (you, never delegated away)
Do not trust either agent's self-report. Run the build and tests yourself and read
the real output:

```
terminal(command="git -C <repo> diff --stat && <project test command>", workdir="<repo>", timeout=300)
```

### Step 5 — Decide and loop
- Tests green and the diff matches the plan -> summarize what each agent did and stop.
- Plan was wrong -> loop to Step 2 (re-plan with the failure as context).
- Implementation wrong but plan right -> loop to Step 3 with the failing output,
  keeping the same plan.
- Never weaken or delete a test to turn the suite green.

## Terminal lifecycle (you own it)

You decide when to open, keep, fork, or end a terminal. Default rules:

- **Open** a dedicated tmux session per active agent:
  `tmux new-session -d -s plan-<task> -x 140 -y 40` (planner),
  `tmux new-session -d -s exec-<task> -x 140 -y 40` (executor). Handle the trust /
  permission dialogs as the `claude-code` skill describes.
- **Keep alive** while the work is multi-turn: you still have follow-up prompts, a
  review-then-fix cycle, or you may need to resume. Monitor with
  `tmux capture-pane -t <s> -p -S -50`; the `>` prompt means it is waiting on you.
- **Fork** when you want a second line of attack without losing history: a parallel
  independent subtask, or trying an alternative approach. Fork the planner's context
  with `claude -r <id> --fork-session`; fork execution by adding a git worktree and a
  fresh session (see Parallel routing). Forking preserves the original session.
- **End** when the unit of work is done and verified, or a session is wedged/looping:
  `tmux kill-session -t <s>`. Always reap finished sessions so the 24/7 host does not
  accumulate leaks; never kill a session that is mid-multi-step work — check progress
  first. End background Codex runs with `process(action="kill", session_id=...)`.

## Slash commands you should know and use

You may drive the agents' own slash commands inside an interactive (tmux) session.

Claude Code (planner): `/plan` (enter plan mode), `/review` and `/security-review`
(before accepting a diff), `/model` and `/effort` (tune the planner), `/context` and
`/compact` (keep context healthy; compact above ~70%), `/cost` (track spend),
`/rewind` (undo a bad step), `/resume` (return to a session), `/agents` (use a
specialized subagent), `/clear` (fresh start). Full list: the `claude-code` skill.

Codex (executor): `codex exec` (one-shot), `codex review` (non-interactive review),
`--full-auto` (sandboxed auto-apply), `--yolo` (no sandbox/approvals), `codex apply`
(apply the agent's last diff), `/approvals` and `/model` inside an interactive
session. Full list: the `codex` skill.

## Parallel routing

For independent subtasks: plan once, then fan out executors over git worktrees so
they cannot collide (one worktree + one session per subtask), monitoring each:

```
git worktree add -b feat/part-a /tmp/part-a <repo>
codex exec --full-auto -m <model> 'Implement part A of the plan: ...'   # workdir=/tmp/part-a, background, pty
```

## Pluggable backends (model-agnostic)

Claude Code and Codex are the default planner/executor because the user chose them,
but the protocol is backend-agnostic. The planner slot can be any agent that emits a
plan (e.g. the `opencode` skill); the executor slot, any agent that applies one. Swap
the skill in the relevant step and keep Steps 1, 4, and 5 unchanged.

**Executor fallback when Codex is unavailable.** If Codex cannot run (for example the
ChatGPT-account model gate above, or no Codex auth at all), do NOT stop the task: keep
the same plan from Step 2 and route execution to Claude Code instead, giving it write
tools so it applies the plan:

```
terminal(command="claude -p 'Implement exactly this plan by creating/editing the files in the current directory, then run the tests: <PLAN>' --allowedTools 'Read Edit Write Bash' --output-format json --max-turns 12", workdir="<repo>", timeout=300)
```

Verify (Step 4) is identical. This keeps the loop working end to end on whichever
executor is actually authenticated; switch back to Codex once its auth is sorted.

## Rules

1. **Plan before execute** — never send a raw request straight to the executor.
2. **Carry the plan faithfully** — the executor only knows what you pass it.
3. **Verify yourself** — run tests/build and read real output before claiming done.
4. **Gate the spend** — both CLIs cost money; confirm scope before a large batch.
5. **Own the terminals** — open, fork, and reap sessions deliberately; leak none.
6. **Stay the router** — delegate planning and execution; do not do their jobs yourself.
7. **Report per agent** — say what the planner planned and what the executor changed.
