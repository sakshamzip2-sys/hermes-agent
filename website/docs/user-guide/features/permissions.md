---
sidebar_position: 7
title: "Permissions & Plan Mode"
description: "Declarative allow/deny/ask rules over tools and commands, plus plan mode — model-agnostic"
---

# Permissions & Plan Mode

OpenComputer lets you declare, in `config.yaml`, exactly which tool calls and
shell commands the agent may run — without writing any code. It also adds a
**plan mode** that lets the agent investigate and propose, but blocks file and
state mutations until you approve.

This works with **any** model or provider you've configured (OpenAI, Gemini,
local models, Anthropic — it's not Anthropic-specific). Rules are pure pattern
matching evaluated on the agent's host, and the permission mode is read live so
toggling it takes effect on the next turn without breaking prompt caching.

:::note Defense in depth, not the only boundary
Permission rules are a policy layer on top of OpenComputer's existing
[approval guards](#how-it-relates-to-approvals) (which still block catastrophic
commands unconditionally). The real isolation boundary is the OS/VM the agent
runs in — see the security model. Use rules to shape day-to-day behavior, not as
a sandbox.
:::

## Quick start

Add a `permissions:` block to `~/.hermes/config.yaml`:

```yaml
permissions:
  mode: normal            # normal | plan | yolo
  allow:
    - "Bash(npm run *)"    # auto-approve matching shell commands
    - "Read(**)"           # allow reading any file
  deny:
    - "Bash(curl * | sh)"  # hard-block curl-pipe-to-shell
    - "Edit(/etc/**)"      # never edit system files
    - "Read(~/.ssh/**)"    # never read private keys
  ask:
    - "Bash(git push *)"   # always prompt before pushing
```

Empty by default — if you don't set `permissions`, nothing changes.

## Rule grammar

Each rule is `ToolName(specifier)`, or a bare `ToolName` to match every call to
that tool. Tool names use a friendly vocabulary that maps onto OpenComputer's
native tools, so you can write `Bash`, `Read`, `Edit`, `WebFetch`, etc.:

| Rule token | Matches |
|------------|---------|
| `Bash(...)` / `Shell(...)` | the `terminal` tool; specifier matches the command |
| `Read(...)` | file-reading tools; specifier matches the path |
| `Edit(...)` / `Write(...)` | `write_file` + `patch`; specifier matches the path |
| `WebFetch(domain:...)` | URL-fetching tools; matches the host |
| `WebSearch`, `Skill`, `Task`, ... | the corresponding tool (bare = any call) |
| `*` | every tool |

Specifiers are shell-style globs (`*` matches across `/`, mirroring
`Bash(npm run *)`). Paths support `~` expansion, so `Read(~/secrets/**)` matches
the user's expanded home path. `WebFetch(domain:example.com)` matches the host
(and `www.example.com`); `WebFetch(domain:*.example.com)` matches subdomains.

### Precedence

When multiple rules match, the decision is resolved in this order:

```
deny  >  allow  >  plan-mode  >  ask
```

- **`deny`** always wins — a hard block the model cannot work around.
- **`allow`** whitelists a call: it skips the approval prompt *and* exempts the
  call from plan-mode blocking.
- **plan mode** blocks mutating calls that aren't explicitly allowed.
- **`ask`** forces an approval prompt (terminal commands in this release).

## Plan mode

Plan mode makes the agent **research and propose, without mutating anything**.
Read-only tools (reading files, listing, grep, non-destructive shell commands
like `ls`/`git status`) stay available so it can investigate; file writes,
patches, and state-changing shell commands are blocked until you leave plan
mode.

Set it persistently in config:

```yaml
permissions:
  mode: plan
```

…or toggle it live in an interactive session:

```
/plan            # enter plan mode (mutations blocked)
/accept-edits    # leave plan mode (write tools restored)
```

`/plan` and `/accept-edits` take effect on the next turn (the system prompt is
rebuilt once so the model knows it's planning — normal sessions are unaffected).

### `yolo` mode

`mode: yolo` (or the existing `--yolo` flag / `approvals.mode: off`) skips all
approval prompts. Catastrophic-command guards still apply.

## Managing rules interactively

```
/permissions                       # show current mode + rules
/permissions mode plan             # set the mode for this session
/permissions test Bash "rm -rf /"  # see what decision a call would get
```

## Headless / scripting

In one-shot mode you can scope tools per invocation (maps onto the same engine):

```bash
oc -z "build the project" --allowedTools "Bash(npm run *),Read"
oc -z "audit deps"        --disallowedTools "Bash(curl *),Edit(/etc/**)"
oc -z "research only"     --max-turns 5
```

`--allowedTools`/`--allowedTools` (both spellings work) and `--disallowedTools`
layer runtime allow/deny rules on top of your config for that run only.

## How it relates to approvals

OpenComputer already prompts before dangerous shell commands
([approvals](/user-guide/features/hooks)). Permission rules sit in front of that:
a `deny` rule blocks before the prompt, an `allow` rule skips the prompt, and an
`ask` rule forces one. The unconditional hardline guards (e.g. `rm -rf /`,
`mkfs`, fork bombs) always run regardless of any rule or mode.

## Subagents

Plan mode propagates to subagents you spawn with `delegate_task` — a delegated
worker can't mutate files while the parent session is in plan mode. Internal
system agents (titles, curation, compression) are unaffected.
