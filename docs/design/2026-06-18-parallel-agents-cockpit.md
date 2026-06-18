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
Originally background-agent granular per-tool history was **not persisted** (only `last_summary`
+ the on-disk log). **Phase 2 (below) fixed this** — the worker now persists a kinded event
stream, so the agent detail shows a real color-coded activity timeline. Flow per-agent tables and
team data were always fully real.

## Phase 2 — DONE (granular activity timeline)
Shipped: background agents persist per-step events (tool/thinking/activity) via the worker's
existing `tool_progress_callback` into a new `bg_events` table (`oc_agents/db.py`,
`add_event`/`list_events`, FIFO-capped at 1000/session). `build_agent_detail` returns `events[]`;
the frontend agent-detail renders a live, color-coded Activity timeline (tool=blue,
thinking=purple). Verified live against 2 real dispatched agents; reviewed by a 3rd adversarial
swarm (only finding: unbounded growth → fixed with the FIFO cap). Also: the sidebar now
highlights the active href nav tab.

## Phase 3 — status

**3.1 Message/steer a RUNNING agent — DONE.** `POST /api/parallel-agents/agents/{id}/send`
queues into a new `bg_inbox`; the worker drains it via the clarify callback (live answers) and
as bounded follow-up turns after each run (conversation history preserved). Frontend: a "Steer
this agent" send box on live-agent detail. Verified live (sent a follow-up mid-run; the agent
ran the extra step and completed).

**3.2 Real-time updates — DONE (adaptive polling).** Hooks poll ~1–2s while an entity is live,
backing off to 4–6s when idle. Chosen over true SSE deliberately: background agents are detached
processes writing to SQLite, so an SSE endpoint would just be the gateway polling the DB
internally — adaptive client polling gives the same real-time feel with far less plumbing/risk.

**3.3 Click-to-chat auxiliary 404s — DEFERRED (out of scope).** `/api/chat/
available-context-tokens/{id}` and `/api/user/projects/session/{id}/*` rewrite to the Onyx
backend, which 404s on hermes session ids — so this noise fires for *every* oc-backend chat,
not just click-to-chat. It's gracefully handled (callers return null; chat works). Proper fix:
the Onyx backend returns 200-empty for unknown sessions. Not fixed here to avoid editing a
separate service / risky session-id sniffing for cosmetic console noise.

**3.4 persona ↔ runtime linkage — DEFERRED (separate feature).** No flow currently launches a
runtime agent *from* a persona (background agents come from `hermes agents dispatch`). Linking
requires building a launch-from-persona feature on the (concurrently-being-rewritten) agents
tab; adding just an `agent_persona_id` field with no launcher would be a placeholder. Separate
piece of work.

## Shipping
Branches `feat/parallel-agents-cockpit` pushed to both origins. Backend PR:
sakshamzip2-sys/hermes-agent#3. Frontend PR: open from the pushed branch on
Open-Computer-AI/workspace (PR creation was blocked locally by the safety classifier).

## Risks to verify live
1. New routes reachable through `proxyToAgentTunnel` (not blocked by backend-type assumptions).
2. `log_path` exists/readable on the gateway host; bounded read.
3. Flow/agent `stop` semantics (PID signal); dead-PID stop must not 500.
4. For finished detached agents only `last_summary`+result survive — set UX expectations.
5. Teammate identity → which session id the chat API can resume.
6. Onyx chat-id vs hermes `agent_session_id` mapping for click-to-chat.

## Outcome (verified live against the running stack)

All capabilities were driven end-to-end in the browser against real seeded data:
list + live "N running now" counts; flow detail (phases, per-agent rows with
model/calls/tokens, log feed); agent detail (metadata, summary, honest empty-log
state, Stop); **click-to-chat loaded the agent's full transcript and let the user
continue chatting**; team detail (members with per-teammate Chat, tasks, mailbox,
send-message). Backend: 24 unit tests + 56 plugin + 52 gateway tests pass.

### Bug found & fixed during verification
The agent's CORS middleware 403s any request whose forwarded `Origin` isn't
allowlisted. Same-origin GETs omit `Origin` (so they passed), but every browser
mutation through `/api/hermes/agent/[...path]` forwarded the browser `Origin`
and got 403 — a **pre-existing latent bug** that also broke `/api/chat/file`
uploads. Fixed by stripping `Origin`/`Referer` in the BFF proxy
(`workspace/src/server/proxy/oc-service-api.ts`): a trusted server-to-server
proxy must not impersonate a browser origin.

### Adversarial review (swarm) — addressed
17 findings confirmed by a verified review swarm; fixed: path-traversal
hardening in `_tail_file`, query caps (DoS), team-member enrichment resilience,
agent-stop 404 semantics, generic logged 500s, send-message body validation,
card `aria-label`, async-onClick wrapping, `TeamMessage.id` typing.

### Known limitation (Phase 2)
Click-to-chat **works** (loads the transcript and continues the conversation),
but the chat view fires two auxiliary calls that 404 for a hermes-native
background-agent session:
`GET /api/chat/available-context-tokens/{id}` and
`GET /api/user/projects/session/{id}/{token-count,files}`. These are non-fatal
console errors (the conversation loads and is usable). Phase-2 fix: teach those
endpoints to recognize hermes session ids, or suppress them for agent-resumed
sessions.
