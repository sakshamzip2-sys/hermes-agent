# Parallel Agents → Live Cockpit (Phase 1) — Design

Date: 2026-06-18
Branch: `feat/parallel-agents-cockpit` (both `OpenComputerV2/` and `workspace/`)
Status: Approved-by-standing-authorization (user scheduled this overnight, autonomy granted)

## Problem

The frontend **Parallel Agents** tab (`workspace/src/refresh-pages/ParallelAgentsPage.tsx`)
is a read-only poll-board. It calls `GET /api/parallel-agents` every 5s and renders three flat
sections of static cards (flows / background agents / teams). It has **no drill-down, no live
per-agent activity, no controls, and no way to chat with an agent** — and it *discards ~70% of
the fields the backend already returns* (model, provider, timings, `agent_session_id`,
`parent_id`, phases, per-flow agents, etc.).

The user wants the experience Claude Code gives when running a swarm: see exactly what each agent
is doing, click any agent to inspect it, and continue chatting with it.

## Create-Agent vs Parallel-Agents (the user's core question)

Keep **both** — they are different planes:

| | Create Agent (`/app/agents`) | Parallel Agents (`/app/parallel-agents`) |
|---|---|---|
| Concept | **Author** reusable personas/assistants | **Watch & steer** live runtime execution |
| Lifetime | Durable templates | Ephemeral instances |
| Data | Onyx persona DB (`lib/agents/*`) | 3 engine SQLite DBs (flow/agents/teams) |
| Verb | **design** | **operate** |

Reconciliation (cheap): tighten both tab subtitles, register `/app/parallel-agents` in the
sidebar focus map so it highlights. (Optional future: stamp `agent_persona_id` into runtime
rows to link "spawned from persona X" ↔ "N live instances". Not in Phase 1.)

## Key facts established by recon (all verified against live code + the running stack)

- Three engines persist full state to SQLite (`~/.hermes/oc_flow.db`, `oc_agents.db`,
  `oc_teams.db`). Each has complete accessors; only a thin slice is exposed over HTTP.
  - `oc_flow/db.py`: `get_run`, `list_runs`, `list_phases`, `list_agents`, `list_logs`, `decode_result`
  - `oc_agents/db.py`: `get_session`, `list_sessions`; `supervisor.snapshot/stop`; rows carry
    `agent_session_id` (= a hermes_state session id), `log_path`, `last_summary`, `api_calls`, …
  - `oc_teams/db.py`: `get_team`, `list_members`, `list_tasks`, `list_messages`, `read_inbox`,
    `send_message`, `team_status_summary`
- The snapshot builder is the testable static `APIServerAdapter.build_parallel_agents_snapshot()`
  (`gateway/platforms/api_server.py:3510`). Endpoints register in the router block (~`:4686`).
  Auth gate: `_check_auth(request)`.
- A full **session-chat** API already exists: `POST /api/sessions/{id}/chat[/stream]`,
  `GET /api/sessions/{id}/messages`, `/fork`. This is the click-to-chat backbone.
- Frontend: the generic proxy `app/api/hermes/agent/[...path]/route.ts` forwards **any method**
  to `<agent>/<path>` → **new backend endpoints need NO new BFF route**. `resolveUserAgent` has a
  local-dev override (`OC_LOCAL_AGENT_URL`), so service-api (:3001) is not required locally.
- Reusable FE: `SettingsLayouts`, Opal `Card`/`Tag`/`Text`/`Button`, the chat-timeline
  primitives, `useChatController`/`sendMessage`/`buildChatUrl`.

## Scope — Phase 1 (this deliverable)

All backend changes are **edge** (gateway handlers + plugin db read accessors). **Zero new core
tools. No core schema change.** Fully model-agnostic (model/provider surfaced generically).

### Backend endpoints (namespaced under `/api/parallel-agents/*`)

Detail (state already exists — endpoint-only):
1. `GET /api/parallel-agents/flows/{flow_id}` → `{run, phases, agents, logs, result}`
2. `GET /api/parallel-agents/agents/{session_id}` → `{session, log_tail}` (bounded read of `log_path`)
3. `GET /api/parallel-agents/teams/{team_id}` → `{team, members, tasks, messages, summary}`

Control:
4. `POST /api/parallel-agents/agents/{session_id}/stop` → `supervisor.stop`
5. `POST /api/parallel-agents/flows/{flow_id}/stop` → signal the flow PID
6. `POST /api/parallel-agents/teams/{team_id}/messages` `{from,to,body}` → `send_message`

Chat bridge:
7. `POST /api/parallel-agents/agents/{session_id}/chat-session` → returns a usable chat
   `session_id` (the agent's `agent_session_id` if present; otherwise create+seed one with the
   agent's prompt/result/log-tail). Frontend then drives the existing `/api/sessions/{id}/chat`.

Implemented as testable static builders (`build_flow_detail`, `build_agent_detail`,
`build_team_detail`) wrapped by thin aiohttp handlers, mirroring the snapshot pattern.

### Frontend (`workspace/`)

8. Register `parallel-agents` in the sidebar focus map; tighten both tab subtitles.
9. Per-entity SWR detail hooks (hook-per-file) via the generic proxy; a live header bar showing
   running counts ("N agents · M flows · K teams running").
10. Master/detail cockpit: clickable cards → detail pane per type, all backed by **real** data:
    - **Flow**: phases (status), agents table (name/model/status/calls/tokens), log feed.
    - **Agent**: prompt, status, model/provider, timings, `last_summary`, log tail, **Stop**,
      **Continue in chat**.
    - **Team**: members, tasks (status tags), mailbox feed, **send-message** box, **chat teammate**.
11. Click-to-chat navigation (resolve the Onyx-vs-hermes session-id mapping empirically; the
    chat-bridge endpoint is the fallback).
12. Detail panes auto-poll while open.

### Error handling
- Missing entity → 404; FE shows "no longer available".
- Detail builders wrap accessors in try/except (graceful degradation like the snapshot).
- `stop` on a dead PID is idempotent (never 500).
- Log tail is byte-capped; missing `log_path` handled.

### Testing / verification
- Backend: extend `tests/test_parallel_agents_endpoint.py` (+ control/chat tests).
- Frontend: `bun run types:check`, `oxlint`, `jest`.
- Live: curl new endpoints against the running gateway (real seeded data exists), browser-drive
  the UI + screenshot, then an adversarial critique swarm → fix loop until green.

## No-placeholder rule
Background-agent granular per-tool history is **not persisted** (only `last_summary` + the
on-disk log). So the agent detail pane shows the **real** log tail + summary — it will NOT fake a
rich per-tool timeline. Flow per-agent tables and team data ARE fully real.

## Phase 2 (explicitly deferred — engine behavior change, higher risk)
- True message-injection into a **running detached** agent (`set_needs_input` resume + input
  channel) — `POST /api/parallel-agents/agents/{id}/send`.
- SSE live push (`/api/parallel-agents/events`) — polling is sufficient for v1.
- `agent_persona_id` linkage between the two tabs.

## Risks to verify live
1. New routes reachable through `proxyToAgentTunnel` (not blocked by backend-type assumptions).
2. `log_path` exists/readable on the gateway host; bounded read.
3. Flow/agent `stop` semantics (PID signal); dead-PID stop must not 500.
4. For finished detached agents only `last_summary`+result survive — set UX expectations.
5. Teammate identity → which session id the chat API can resume.
6. Onyx chat-id vs hermes `agent_session_id` mapping for click-to-chat.
