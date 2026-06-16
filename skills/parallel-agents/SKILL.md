---
name: parallel-agents
description: "Run agents in parallel — dynamic workflows (hermes flow), background agent sessions (hermes agents), and agent teams (hermes team). Use when a task benefits from many subagents, fan-out/verify pipelines, dispatching long-running work to the background, or a lead+teammates split with a shared task list."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  opencomputer:
    tags: [parallel, subagents, workflow, background, team, orchestration, fan-out, dispatch]
    related_skills: []
---

# Running Agents in Parallel

OpenComputer v2 ships three complementary ways to run more than one agent at
once. Pick the one whose **shape** matches the task.

| Capability | Command | Use when |
|---|---|---|
| **Dynamic workflows** | `hermes flow` (`/flow`) | The plan is worth codifying: fan-out across many files/sources, a find→verify pipeline, a migration, or a question that needs cross-checking. The orchestration lives in a **resumable script**. |
| **Agent view** | `hermes agents` (`/bgagents`) | You want to **dispatch a task and walk away** — a bug fix, a long investigation — and check back later. Each runs as a detached background session you can list/follow/stop. |
| **Agent teams** | `hermes team` (`/team`) | The work splits into roles that must **coordinate** — a lead plus teammates sharing a task list and messaging each other. |

## Dynamic workflows — `hermes flow`

A flow is a Python script with injected helpers: `agent()`, `parallel()`,
`pipeline()`, `phase()`, `log()`, `args`, `result()`. The engine drives real v2
subagents, persists every run, and can resume.

```bash
hermes flow run path/to/flow.py --args '["src/a.py","src/b.py"]'
hermes flow run path/to/flow.py --background      # detached worker
hermes flow run path/to/flow.py --resume <RUN_ID> # cached agents skipped
hermes flow list ; hermes flow show <RUN_ID> ; hermes flow logs <RUN_ID>
hermes flow examples                              # bundled example flows
```

Minimal flow:

```python
META = {"name": "review", "phases": ["Review", "Verify"]}
phase("Review")
findings = parallel([(lambda f=f: agent(f"review {f} for bugs", schema=SCHEMA)) for f in args])
phase("Verify")
confirmed = pipeline(findings, lambda f: agent(f"adversarially verify: {f}"))
result(confirmed)
```

`parallel()` runs callables concurrently (cap respected); `pipeline()` flows each
item through all stages independently. Set `OC_FLOW_FAKE_AGENT=1` to smoke-test a
flow's structure without spending tokens. See `_flow_api.py` for typed signatures.

## Agent view — `hermes agents`

```bash
hermes agents dispatch "investigate the flaky CheckoutTest" --name flaky
hermes agents list [--all]            # state: working/needs_input/completed/failed
hermes agents show <id> ; hermes agents logs <id>
hermes agents attach <id>             # follow live until it ends (Ctrl-C detaches)
hermes agents stop <id> ; hermes agents rm <id>
```

Each session is a detached process that self-reports to a registry; a dead
worker is reconciled to `failed` on the next `list`. No daemon to manage.

## Agent teams — `hermes team`

```bash
hermes team create "research" --goal "find the root cause"
hermes team spawn <team_id> alice "investigate the auth path" --role security
hermes team task-add <team_id> "write the fix" --depends <task_id>
hermes team tasks <team_id> --status claimable
hermes team show <team_id>
hermes team cleanup <team_id>
```

Teammates run as background sessions with `HERMES_TEAM_ID` in their env, which
unlocks the **service-gated team tools** in their session: `team_status`,
`team_list_tasks`, `team_claim_task`, `team_complete_task`, `team_create_task`,
`team_send_message`, `team_read_inbox`. Task claiming is atomic (only one
teammate wins a task) and tasks can depend on other tasks (a dependent task is
not claimable until its dependencies complete).

## Choosing

- One self-contained task you'll walk away from → **agents**.
- A repeatable, codified fan-out / verify pipeline → **flow**.
- Roles that must talk and reconcile while working → **team**.
- A quick, in-conversation delegation where only the result matters → the
  built-in `delegate_task` tool (no orchestration overhead).

## Reusable agent-type definitions

Define a specialized agent once and reuse it everywhere. Drop a Markdown file
with YAML frontmatter at `.hermes/agents/<name>.md` (project) or
`~/.hermes/agents/<name>.md` (user; project wins on a name clash):

```markdown
---
name: code-reviewer
description: Reviews diffs for bugs and security. Use after edits.
tools: [read_file, web]        # restricted toolsets (alias: toolsets:)
model: claude-haiku-4-5        # optional per-agent model (model-agnostic)
permissionMode: plan           # optional: start read-only (normal|plan|yolo)
memory: project                # optional: own MEMORY.md (user|project|local)
---
You are a meticulous reviewer. Report Critical / Warning / Suggestion.
```

The body is the agent's system prompt. Use it across all three surfaces (the
definition seeds persona/toolsets/model; explicit args still win):

```bash
hermes team defs                                   # list available definitions
hermes team spawn <team_id> alice "review auth" --agent code-reviewer
hermes agents dispatch "audit the API" --agent code-reviewer
```

and the in-conversation `delegate_task` tool accepts `agent_type` (+ a per-call
`model`) on the top-level call or per task in a batch.

- **`permissionMode: plan`** spawns the teammate read-only (can research, can't
  mutate) until the lead is satisfied — the headless equivalent of CC's
  teammate plan-approval gate. Also `hermes team spawn … --permission-mode plan`.
- **`memory: project`** gives that agent its own `.hermes/agent-memory/<name>/`
  that persists across runs, so it accumulates patterns over time.

## Team quality gates (hooks)

Agent teams fire veto-capable lifecycle hooks so a plugin or shell script can
gate work even with no human watching:

| Hook | Fires | Veto effect |
|---|---|---|
| `team_task_created` | a teammate adds a task | block → task not created |
| `team_task_completed` | a teammate marks a task done | block → completion refused, reason fed back |
| `team_teammate_idle` | a teammate finishes with no claimable work | message → "keep working" nudge |

Veto with the canonical `{"action":"block","message":"…"}` (or Claude-Code's
`{"decision":"block","reason":"…"}`); a shell hook may also block by exiting 2.
Use `team_task_completed` to require evidence (a passing test, a log line)
before a task counts as done. Dry-run a gate script with
`hermes hooks test team_task_completed`.
