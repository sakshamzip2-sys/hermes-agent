# proactivity — event check-ins plugin

Ports OpenComputer **v1's** proactivity subsystem (the "productivity feature" from
`intelligence/proactivity/`) into **v2** as an edge plugin.

## What it does

Tracks events the user is attending (told the agent about). After an event ends it
surfaces a warm, in-context check-in ("how'd X go?"), captures the reply into
`MEMORY.md` (the learning loop), and adapts its reminder cadence from the user's
feedback ("stop reminding me so much" → tighten; "you never check in" → loosen;
"mute the calendar stuff" → mute).

## PROTECTED INVARIANT — default-OFF, consent-gated

Carried verbatim from v1 (a documented owner invariant): **proactive surfacing is
default-OFF**. Installing the plugin does nothing until `proactivity.enabled: true`
(or `hermes proactivity enable`). Surfacing is **in-context only** — delivered through
the `pre_llm_call` hook while the user is already chatting. There is **no out-of-band
push** in this port, so the user is never messaged unprompted. The gate further
guarantees:

- Only `TOLD_FACT` / `USER_LOOP` sensitivities are ever push-eligible.
- `SENSITIVE` events are hard-suppressed (never surfaced).
- Muting is subtractive-only; auto-tuned cadence can never exceed a hard cap.

## Files

| File | Role |
|------|------|
| `models.py` | `TrackedEvent`, `EventState`, `Sensitivity`, `SurfaceTier`, `EventContext` |
| `gate.py` | Deterministic `decide_tier` + warm check-in renderers (pure) |
| `cadence.py` | Self-evolving push cap + mute keywords (bounded, fail-soft) |
| `feedback.py` | NL + behavioural cadence signals (pure, regex) |
| `store.py` | SQLite event store + lifecycle state transitions |
| `surface.py` | Per-turn: capture reply → apply feedback → gate → inject one check-in |
| `writeback.py` | Closed check-in reply → `MEMORY.md` (two-hats: user words only) |
| `config.py` | `proactivity:` block loader (`enabled` defaults to **False**) |
| `cli.py` | `hermes proactivity {status,track,enable,disable}` |
| `__init__.py` | `register(ctx)` — `pre_llm_call` hook + `/track` + `/proactivity` |

## How v2 differs from v1 (deferred scope)

The pure core (tracking + gate + cadence + feedback + writeback) is ported faithfully.
Deferred because they depend on v2 infrastructure that differs or is absent:

- **Out-of-band PUSH delivery** (v1 `OutgoingQueue` + channels) — this port surfaces
  in-context only; the `PUSH` tier is computed but never delivered out-of-band.
- **Sensors** (v1 calendar / Luma discovery) — events are added via `/track`, not
  auto-discovered.
- **Agentic action lane** (v1 read-only drafting subagent) and the cross-system
  shared-send rate ledger.
- **Memory-aware `EventContext`** (name + history from MEMORY.md) is stubbed empty;
  renderers fall back to the byte-stable legacy strings.

## Usage

```
hermes proactivity enable                 # opt in (consent gate)
hermes proactivity track "infra meetup"   # an event that just ended
/track dentist in 2h                       # an upcoming event (in-session)
hermes proactivity status
```

## Config

```yaml
proactivity:
  enabled: false        # INVARIANT: default-OFF, consent-gated
  push_cap_per_day: 1
  quiet_start_hour: 22
  quiet_end_hour: 8
  cadence_evolution: true
  event_ttl_days: 14
```

## Tests

`tests/plugins/test_proactivity_*.py`. Run:

```
.venv/bin/python -m pytest tests/plugins/test_proactivity_*.py -q -p no:cacheprovider
```
