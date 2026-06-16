# Running Agents in Parallel

OpenComputer v2 ships four ways to run more than one agent at once. Three are new
plugins (at the edge — no new always-on core tools); the fourth (worktrees) was
already in the core CLI and is now also available per-subagent inside flows.

All three plugins follow the same v2 idiom: a **standalone SQLite DB** under the
hermes root (never touching the core `hermes_state` schema), CLI commands + a
slash command registered via the plugin system, and — for teams — model tools
that are **service-gated** so a normal session's tool schema is unaffected.

| Concept (Claude Code) | v2 plugin | CLI | Slash | DB |
|---|---|---|---|---|
| Dynamic workflows | `oc_flow` | `hermes flow` | `/flow` | `oc_flow.db` |
| Agent view | `oc_agents` | `hermes agents` | `/bgagents` | `oc_agents.db` |
| Agent teams | `oc_teams` | `hermes team` | `/team` | `oc_teams.db` |
| Worktrees | (core `hermes -w`) + `oc_flow` `agent(worktree=True)` | — | — | — |

Enable them (opt-in, like all standalone plugins):

```bash
hermes plugins enable oc_flow
hermes plugins enable oc_agents
hermes plugins enable oc_teams
```

or add to `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled: [oc_flow, oc_agents, oc_teams]   # plus whatever you already have
```

See the **parallel-agents** skill (`skills/parallel-agents/SKILL.md`) for the
full command reference and a worked flow example. The short version:

## Dynamic workflows — `hermes flow`

A flow is a trusted, local Python script with injected helpers (`agent`,
`parallel`, `pipeline`, `phase`, `log`, `args`, `result`). The engine drives real
v2 subagents (built exactly like `hermes -z`), persists every run, runs in the
background, and **resumes** (completed agents are skipped via a content-addressed
cache, so the resume key survives non-deterministic fan-out ordering).

```bash
hermes flow run flow.py --args '["a.py","b.py"]'   # foreground
hermes flow run flow.py --background               # detached worker
hermes flow run flow.py --resume <RUN_ID>          # cached agents skipped
hermes flow list | show <id> | logs <id> | stop <id> | examples
OC_FLOW_FAKE_AGENT=1 hermes flow run flow.py       # offline structure smoke-test
```

`agent(prompt, worktree=True)` runs that subagent in its own git worktree
(auto-removed if it made no changes), so parallel file-editing agents don't collide.

## Agent view — `hermes agents`

Dispatch headless agent sessions that run **detached** and self-report to a
registry. No daemon: a dead worker is reconciled to `failed` on the next `list`.

```bash
hermes agents dispatch "<task>" [--name N] [--model M] [--cwd DIR]
hermes agents list [--all] | show <id> | logs <id> | attach <id> | stop <id> | rm <id>
OC_AGENTS_FAKE_AGENT=1 hermes agents dispatch "<task>"   # offline smoke-test
```

## Agent teams — `hermes team`

A lead and teammates share a task list (with dependencies + atomic claiming) and
a mailbox. Teammates run as `oc_agents` background sessions with `HERMES_TEAM_ID`
in their env, which unlocks the service-gated team tools (`team_status`,
`team_claim_task`, `team_complete_task`, `team_create_task`, `team_send_message`,
`team_read_inbox`, `team_list_tasks`) in their session.

```bash
hermes team create "<name>" --goal "<goal>"
hermes team spawn <team_id> <member> "<prompt>" [--role R]
hermes team task-add <team_id> "<subject>" [--depends t1,t2]
hermes team tasks <team_id> --status claimable
hermes team show <team_id> | cleanup <team_id> [--force]
```

Task claiming is an atomic compare-and-swap (only one teammate wins a task);
dependent tasks aren't claimable until their dependencies complete; a broadcast
message is independently readable by each member.

## Choosing

- One self-contained task you'll walk away from → **agents**.
- A repeatable, codified fan-out / verify pipeline → **flow**.
- Roles that must talk and reconcile while working → **team**.
- A quick in-conversation delegation where only the result matters → the built-in
  `delegate_task` tool.

## Reusable agent-type definitions, quality gates, plan mode, per-agent memory

Beyond the three runners, v2 ports the rest of the Claude-Code subagents/teams
surface in the v2 idiom (no new always-on core tool):

- **Agent-type definitions** — `.hermes/agents/<name>.md` (project) or
  `~/.hermes/agents/<name>.md` (user): YAML frontmatter (`name`, `description`,
  `tools`/`toolsets`, `model`, `provider`, `permissionMode`, `memory`, `effort`,
  `maxTurns`) + a Markdown body (the system prompt). Reuse one definition across
  `delegate_task(agent_type=…)`, `hermes team spawn … --agent <name>`, and
  `hermes agents dispatch … --agent <name>`. List with `hermes team defs`.
  `delegate_task` also gained an optional per-call `model` (model-agnostic).
- **Team quality gates** — `oc_teams` fires `team_task_created` /
  `team_task_completed` / `team_teammate_idle` hooks; a plugin or shell script
  (exit 2, or `{"action":"block","message":…}`) can veto task completion until
  evidence is attached — the headless replacement for a human eyeballing results.
- **Teammate plan mode** — `--agent` with `permissionMode: plan` (or
  `hermes team spawn … --permission-mode plan`) starts a teammate read-only;
  honored process-wide via the permission_rules engine.
- **Per-agent memory** — `memory: project|user|local` gives a spawned agent its
  own `MEMORY.md` that persists across runs, isolated from global memory.

See the **parallel-agents** skill for examples.
