# Specialized Agents on Gateway Channels — PROGRESS

## Verbatim request (never edit)
> i want you to connect all the specialed agents to the gateway to all of them namly
> whatsapp telegram whatsapp business discord slack i want all of the agents to connect
> to gatways to chat since they are all profiles this should be possible so i want you to
> do this now /max-effort keep going /goal and do not stop untill you have done everything
> do thisnow

## Done-criteria (graded against the original)
1. On any messaging channel (Telegram, WhatsApp, WhatsApp Business, Discord, Slack — and
   the rest, since the mechanism is channel-agnostic) a user can select and chat with any
   shipped specialized agent (finance, legal, deep-research, knowledge-work, atlas, sage,
   ledger, coder, ...).
2. The selected agent runs with ITS OWN identity (SOUL/membrane), toolsets, model, and
   ISOLATED memory/state.db — the same isolation the web UI gives via `oc_agent_id`.
3. Selection is per-chat and switchable at runtime; reversible.

## Key architecture facts (verified by reading the code)
- Web UI selects a specialized agent via per-request `oc_agent_id` (api_server.py). The
  gateway channels normalize to a `MessageEvent` that has NO agent selector, and the native
  agent build (`run.py:_run_agent` → AIAgent ~14891) passes no slug → always default agent.
- api_server runs a profile turn by: `set_hermes_home_override(agent-profiles/<slug>)`
  (loads SOUL/membrane; CONTEXTVAR → per-task safe), isolated `agent-profiles/<slug>/state.db`
  as the agent's `session_db`, `_apply_agent_def`→`resolve_agent_overrides(slug)` (toolsets+model),
  and `disable_memory_provider=True` + drop `gbrain` toolset. (api_server.py:5320-5402, 1219-1369)
- Native path persistence: post-run uses `skip_db=agent_persisted` — the AGENT is the DB of
  record via its `session_db` (run.py:9392-9395). So isolating persistence == giving the agent
  the profile db. History LOAD is a single site: `run.py:8596 self.session_store.load_transcript`.
- Only 3 `self._session_db` refs inside `_run_agent` body (14845/14847/14920) → clean `turn_db` local.
- `/agents` (plural) is TAKEN (active-tasks cockpit). `/agent` (singular) is FREE → use it.
- Shipped specialized agents = `profile_templates/` dirs: atlas, coder, deep-research,
  finance, knowledge-work, ledger, legal, sage. Manifests in `.hermes/agents/*.md` (toolsets/model).

## Design (locked)
- New module `gateway/persona_bindings.py`: per-chat (platform, chat_id, thread_id) → slug,
  atomic JSON under hermes home; + a catalog of known agents (profile_templates ∪ agent-profiles
  ∪ .hermes/agents) and slug validation. Pure + unit tested.
- `/agent` command: `/agent` (show current + list), `/agent <slug>` (bind + rotate session),
  `/agent off` (clear). Registered in hermes_cli/commands.py; handler `_handle_agent_command` in run.py.
- Native persona application in `_run_agent` (when chat bound): home override + profile_db as
  agent session_db + turn_db at the 3 refs + resolve_agent_overrides(toolsets,model) + drop gbrain
  + disable_memory_provider; history loaded from profile_db at run.py:8596; reset in finally.
- Proxy seam: forward bound slug as `oc_agent_id` in `_run_agent_via_proxy` (reuses the fully
  tested api_server path for proxy deployments).

## Rubric (pass = every applicable dim ≥4, zero open Correctness/Security, out-of-band yes)
Correctness · Completeness · Robustness · Security · Simplicity · Tests

## Gates / can't-self-verify
- LIVE per-channel send (Telegram/WhatsApp/Discord/Slack) needs the user's bot tokens AND
  sends external messages → GATED. Verify mechanism via unit tests + local boot smoke instead.

## Iteration log
- (init) Recon complete; branch feat/specialized-agents-on-channels; design locked. Building.
- (design review, independent subagent) CONFIRMED: contextvar home-override is thread-safe via
  `_run_in_executor_with_context`/copy_context (run.py:12191) → set override BEFORE the executor
  dispatch (~15859). CONFIRMED: AIAgent persists to its own session_db (run_agent.py:1508-1517),
  so passing profile_db as the agent's session_db gives correct isolation. MUST-FIX folded in:
  (a) `/agent <slug>` rotates to a fresh session_id on bind + load history from profile_db when
  bound; (b) include persona slug in `_agent_config_signature` so a cached default agent is not
  reused for a bound run.
- (build) Phase 1 (bindings+catalog, 28 tests) + Phase 2 (/agent command, 9 tests) committed.
  Phase 3 (native turn application) in run.py: __init__ profile-db cache; _resolve_chat_persona
  + _get_persona_profile_db helpers; handle_message sets HERMES_HOME override + profile dir
  (reset in finally) and loads history from the profile db when bound; _run_agent threads
  persona_slug/persona_db -> turn_db (3 refs), gbrain drop + resolve_agent_overrides
  toolsets/model, slug folded into agent-cache signature, disable_memory_provider, followup
  call threads it through; proxy path forwards slug as oc_agent_id. Runtime tests (6) added.
- (verify) 43 persona tests PASS; 35 existing dispatch/session/hygiene tests PASS; ruff clean;
  gateway.run imports cleanly (boot smoke). Broad-suite failures (wecom, update_command, tui
  custom-provider, telegram-escaping) PROVEN pre-existing: identical with my run.py edits
  stashed; failing cases pass in isolation (pollution) or are rebrand string mismatches.
  => zero regressions from this work.
- GATED (unchanged): live per-channel send needs the user's bot tokens + sends external
  messages. Mechanism verified by tests + boot smoke; live verification is the user's step.
