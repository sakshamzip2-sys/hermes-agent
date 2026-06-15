# proactivity — a general, source-agnostic proactivity engine

Proactivity is a **pipeline**, not a sensor list: pluggable sources emit
`ProactiveMoment`s → a deterministic gate (motivation × timing × budget × quiet-hours)
→ delivery (in-context, or out-of-band **push / digest through the gateway**). Calendar/
email/Luma are just future sources behind the same interface — none are the core.

Design + research basis: `docs/superpowers/specs/2026-06-15-proactivity-and-dreaming-v2-design.md`.

## Pipeline

```
sources ──poll()──> ProactiveMoment ──> gate ──> delivery
 commitment          {category, urgency,  motivation   in-context (pre_llm_call hook)
 event-tracker        sensitivity,         × budget     push  (gateway: cron _deliver_result)
 inactivity           confidence, TTL,     × timing     digest(gateway)
 (calendar/email…)     dedup, reasoning}   × quiet-hrs
```

| Unit | Role |
|------|------|
| `moment.py` | `ProactiveMoment` + 11 `Category`s + `MomentState` |
| `sources/` | `ProactiveSource` interface + `commitment`, `event_tracker`, `inactivity` |
| `moment_gate.py` | deterministic `decide` → INJECT / PUSH / DIGEST / DROP (+ `score_moment`) |
| `moment_store.py` | moment persistence (dedup) + shared notification-budget ledger |
| `gateway_delivery.py` | push/digest OUT via `cron.scheduler._deliver_result` (the gateway path) |
| `engine.py` | orchestrator: cheap in-context surfacing + full background poll/push/digest |
| `session_reader.py` | read conversation history from `state.db` (read-only) |
| `llm.py` | aux-LLM commitment extraction (cheap regex pre-filter) |
| `store.py` / `gate.py` / `cadence.py` / `feedback.py` | tracked-events store + bounded self-tuning (kept from v1) |
| `__init__.py` / `cli.py` | `pre_llm_call` hook + `/track` `/proactivity` `/commitments` + `opencomputer proactivity {status,track,run,enable}` |

## The sources (highest-value first)

1. **commitment** — extracts "you said you'd do X / remind me to Y" from recent
   conversation via the aux LLM (regex pre-filter gates the paid call). The killer
   feature: a personal agent owns the chat log; Google/Apple don't.
2. **event-tracker** — the `/track` flow; emits a check-in when a tracked event ends.
3. **inactivity** — gentle re-engagement after a long silence. Push-FORBIDDEN
   (re-engagement is the highest-abuse category) → only ever in-context or digest.

New sources are drop-in: implement `ProactiveSource.poll(ctx) -> list[ProactiveMoment]`.

## PROTECTED INVARIANT — default-OFF, consent-gated

`proactivity.enabled` defaults **False**. In-context surfacing (free, preferred) rides
the `pre_llm_call` hook; out-of-band **push only fires for urgent, push-eligible moments
that clear the full gate** (budget, quiet hours), delivered through the gateway. The gate
hard-suppresses `SENSITIVE`, only allows push for `TOLD_FACT`/`USER_LOOP` sensitivities,
never pushes re-engagement, and respects a daily notification budget (default 3).

## How push/digest goes "through the gateway"

`gateway_delivery.deliver()` calls v2's proven cron outbound path
(`cron.scheduler._deliver_result` → live `adapter.send` or the standalone platform
sender), so a proactive message reaches the user's configured home channel(s) exactly
like a cron job's output. Trigger the background cycle with `opencomputer proactivity run`
(or schedule it as a cron job).

## Config

```yaml
proactivity:
  enabled: false                 # default-OFF, consent-gated
  push_cap_per_day: 3            # notification budget
  quiet_start_hour: 22
  quiet_end_hour: 8
  min_motivation: 3             # motivation score (1-5) required to surface
  inactivity_days: 7
  recent_window_days: 7
  background_interval_minutes: 30
auxiliary:
  proactivity: { provider: auto }  # model for commitment extraction
```

## Tests

`tests/plugins/test_proactivity_*.py` — moment/gate/store, engine end-to-end (surface,
push, digest, capture-reply), sources (commitment, inactivity), gateway delivery, plus
the real-loader + hook-contract tests. ~110 tests.
