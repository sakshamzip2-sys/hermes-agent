# Memory Part 2 — Genuine-Gap Map and Build Plan (Cross-Agent Outcome Observability + Evaluation)

Branch `feat/memory-mission`, Hermes `0.16.0` (verified: `pyproject.toml:10`). All file:line and doc claims below were re-verified against the real repo in this worktree and against the Nous docs. No em dashes.

The directive premise is correct but must be narrowed twice over: Hermes already ships (a) the entire self-improvement / skill-lifecycle / rollback substrate AND (b) most of the observability+eval half too (the `outcomes` evaluator and a first-party `langfuse` tracer). The genuine gap is not "build observability" — it is **join three things that already exist** (the turn_score evaluator, the Langfuse trace exporter, and the cross-agent subagent hooks) and **add one dimension** (agent/run identity) so "which agent produced a good run" becomes answerable. Everything else is reuse or out of scope.

---

## 1. What Hermes 0.16.0 ALREADY ships for self-improvement (DO NOT rebuild)

| Subsystem | Reuse surface (exact seam) | What it gives you |
|---|---|---|
| **Curator (skill lifecycle + analytics)** | `agent/curator.py` `apply_automatic_transitions()` (:276-331), config getters `get_stale_after_days`/`get_archive_after_days` (:162-173), `DEFAULT_CONSOLIDATE=False` (:64), review prompt (:385-471); CLI shell `hermes_cli/curator.py` (status/run/prune/backup/rollback) | active to stale (30d) to archived (90d) deterministic walk, pinned-skip, reactivate-on-reuse, opt-in LLM consolidation. **Live config confirmed** `~/.hermes/config.yaml:460-469` (enabled true, interval_hours 168, min_idle_hours 2, stale 30, archive 90, prune_builtins true, backup keep 5). |
| **Skill usage sidecar** | `tools/skill_usage.py` `_empty_record()` (:460-473), `bump_use`/`bump_view`/`bump_patch` (:588-619); counters wired at `skill_run_tool.py:181-188`, `skills_tool.py:1606-1628`, `skill_manager_tool.py:1079-1090` | per-skill `use_count`/`view_count`/`patch_count`/timestamps in `~/.hermes/skills/.usage.json` (atomic write + flock). **Confirmed by grep:** record has NO `success_rate`/`avg_latency`/`cost_per_run`/`user_rating`. |
| **Self-improvement trigger** | system-prompt nudge `agent/prompt_builder.py:174` + `tools/skill_manager_tool.py:1116` ("create when complex task succeeded, 5+ calls"); `skills.write_approval` staging to `~/.hermes/pending/skills/` | model writes/patches skills after complex tasks; risky writes review-gated via `/skills approve`. **Docs confirm** (skills page): auto-create after "5+ tool calls successfully", "patch is preferred", every `skill_manage` write staged. |
| **Idle background fork (dreaming)** | `plugins/dreaming/__init__.py:138-139` registers `on_session_start`/`on_session_end` to `maybe_run_in_background`; `runner.py:323-339` (daemon thread + `_run_lock` non-blocking debounce); `run_dream_cycle` body at `runner.py:94`; reads `recent_turn_scores` to tune its bar | the exact non-blocking, debounced, fail-soft scheduler seam a reflection PROPOSAL pass plugs into. **Do not fork a parallel scheduler.** |
| **Outcomes evaluator (the "was it good" organ)** | `plugins/outcomes/store.py` `turn_outcomes` schema (:20-30) at `$HERMES_HOME/dreaming/outcomes.db`, read seams `recent_turn_scores`/`recent_session_scores` (:92-121, :176-188); `engine.py` composite+judge fusion; `judge.py` aux-LLM verdict (model-agnostic via `auxiliary_client`); register hooks `__init__.py:182-184` | per-turn `turn_score` in [0,1] = free composite signal fused with opt-in aux-LLM judge. Default OFF in code (`config.py:19-20`) but **LIVE config has it ON** (`~/.hermes/config.yaml:692-694` enabled true, judge_enabled true). |
| **Rollback / safety substrate** | `agent/curator_backup.py` `snapshot_skills()` (:211) + `rollback()` (:539), auto-snapshot pre-run `curator.py:1485`; `tools/checkpoint_manager.py` (shadow git store, per-turn snapshot, `/rollback`); `agent/memory_versioning.py` (content-addressed MEMORY.md/USER.md, `restore`/`redact`); `plugins/dreaming/review.py` (HMAC-chained proposal queue, `queue_pending`/`record_rollback`/`verify_chain`) | versioned + reversible + approved self-modification already exists for skills, files, memory, and dream proposals. **Any score-driven change must plug into these, not duplicate them.** |

Decisive: the Curator tracks **usage, never outcome quality**, and its review prompt explicitly forbids using usage as a quality signal (`curator.py:391-394`: "use=0 is not evidence a skill is valuable; it's absence of evidence either way"). So a quality column is genuinely additive, not a duplicate.

Docs corroboration: Nous skills page confirms the Curator/5+-call/patch/write-approval set; built-in-plugins page confirms `observability/langfuse` traces "one span per turn, one generation per API call, one tool observation" with token usage and cost but documents **no quality scoring or evaluation framework**; there is **no observability/telemetry/eval doc page at all** (`/docs/observability` and `/docs/curator` return 404).

---

## 2. The tracing decision: native hook vs hooks-system vs Langfuse SDK

**Decision: reuse the existing native observer-hook contract and EXTEND the already-shipped `langfuse` plugin. Do NOT hand-roll SDK spans in the agent loop, and do NOT add OpenTelemetry.**

Why, with the concrete seam:

1. **A native hooks contract already carries the full per-run trace.** `OBSERVER_SCHEMA_VERSION` is defined (`hermes_cli/middleware.py:17`) and injected into every hook payload by `invoke_hook` (`hermes_cli/plugins.py:1705`), gated by `has_hook`. The agent loop already emits `on_session_start`, `pre/post_api_request` (with task_id, turn_id, model, provider, usage, cost, finish_reason), `pre/post_llm_call`, `pre/post_tool_call`, `on_session_end`, plus the cross-agent links `subagent_start` (`tools/delegate_tool.py:1428-1440`) and `subagent_stop` (`tools/delegate_tool.py:2658-2695`). The full contract is spec'd in `docs/observability/README.md`.

2. **A Langfuse consumer of that contract already ships, fail-open and opt-in.** `plugins/observability/langfuse/__init__.py` builds the per-turn trace, mints a deterministic `create_trace_id(seed="{session}::{task}")` (:606), emits generations with usage/cost from `agent.usage_pricing`, groups by session_id, and registers 6 hooks at :1128-1137. A second sink (`nemo_relay`) proves the fan-out pattern. Per `AGENTS.md`, this stays opt-in and env-gated (no un-gated outbound telemetry).

3. **Raw SDK / OTel is the wrong layer.** Grep confirms no `langfuse`/`otel` call anywhere in the agent loop (the only `otel` hits are two unrelated skill scripts). Hand-rolling spans would duplicate the observer contract and violate the v2 "capability at the edges" rule (`AGENTS.md` Footprint Ladder: new core code is the last resort).

**One drift to fix while here:** the code constant is `hermes.observer.v1` (`middleware.py:17`) but `docs/observability/README.md:42` says `opencomputer.observer.v1`. The doc is stale relative to the rebrand. Trivial S fix, surfaced for the user (do not silently change the runtime constant without confirming which is canonical).

Cheapest reuse path therefore: **register two more hooks on the existing langfuse plugin + write a small outcomes-to-Langfuse score bridge**, rather than any new tracer.

---

## 3. The genuine gap, item by item (P2-1 .. P2-6)

| Item | Verdict | Exact seam | Effort |
|---|---|---|---|
| **P2-1 Tracing** (goal, prompt, memory hits, tool/MCP/model calls, tokens, cost, latency, output, feedback) | **MOSTLY ALREADY-EXISTS-REUSE; one GENUINE-GAP slice** | Reuse `plugins/observability/langfuse/__init__.py` (cost/latency/tokens/tools already traced). GAP = cross-agent: `register()` (:1128) does NOT subscribe `subagent_start`/`subagent_stop`, so delegated/team runs are orphan traces. Add those 2 hooks + parent trace-context using the parent/child IDs already on the wire (`delegate_tool.py:1428-1440`, :2658-2695). Memory/retrieval hits are NOT yet a trace attribute (add via the MergeLayer `RecallTrace` already built in Part 1). | **S** (2 hooks) + **S** (recall attr) |
| **P2-2 Evaluator** (score completed runs, store against traces) | **EVALUATOR EXISTS; the JOIN is the GENUINE-GAP** | Scoring exists: `plugins/outcomes/engine.py` + `judge.py` produce `turn_score`, persisted to `turn_outcomes` (`store.py:20-30`). GAP (verified by empty grep `create_score|score` in the langfuse plugin) = the verdict is NEVER attached to the trace. Build a thin bridge that reads `recent_turn_scores`/the matching row keyed by session_id+turn and calls Langfuse `create_score` on the trace minted with the same seed (`__init__.py:606`). Do NOT rebuild scoring. | **M** (bridge + keying) |
| **P2-3 Close loop via existing Curator** | **GENUINE-GAP-BUILD (additive, no parallel system)** | The Curator usage record has no quality column and the review prompt forbids usage-as-quality (`curator.py:391-394`). Build: attribute a per-run outcome to the skill(s) used at the existing join points (`skill_run_tool.py:181-188`, `skills_tool.py:1606-1628`) by writing an additive `success_rate`/`avg_latency`/`cost_per_run`/`user_rating` rollup into the sidecar (`tools/skill_usage.py` `_empty_record` :460-473), then surface it in the existing review render (`curator.py:1419-1423` / `agent_created_report` :823-846) as a **read-only signal the human reviewer sees** — never an auto-prune trigger (respect the prompt's usage-is-not-quality rule). | **M** |
| **P2-4 Dreaming = scheduled reflection PROPOSAL queue** | **ALREADY-EXISTS-REUSE for the mechanism; GENUINE-GAP for the proposal content** | Reuse the idle fork verbatim: register the reflection pass on `on_session_start`/`on_session_end` via `maybe_run_in_background` (`runner.py:323-339`) or call it from `run_dream_cycle` (`runner.py:94`); reuse the HMAC review queue (`plugins/dreaming/review.py`). GAP = the pass that reads `recent_session_scores` + low-scoring traces and writes a human-readable proposal. **It must write to `PROPOSALS.md` and the review queue only, never auto-apply.** | **M** |
| **P2-5 Memory-layer utility scoring** (used+helpful promoted, unused decays) | **MOSTLY ALREADY-EXISTS-REUSE** | Procedural (skills) = Curator lifecycle already does decay/archive. Semantic (USER.md/Honcho) = already versioned. Episodic (traces) = the new outcomes-scored traces from P2-1/P2-2. GAP is only the unifying "utility = used x helpful" view, which is a read-only rollup over `turn_outcomes` + skill sidecar + Part 1 MergeLayer promotion band; no new store. | **S-M** (read-only view) |
| **P2-6a Skill metrics layer** (success_rate, avg_latency, cost_per_run, user_rating + health view) | **GENUINE-GAP-BUILD** | Same additive columns as P2-3; this is the surfaced "skill health" view over the sidecar + the per-run attribution. | **M** |
| **P2-6b Skill versioning + A/B** | **PARTIALLY-EXISTS; A/B is GENUINE-GAP** | Versioning/rollback already exists (`curator_backup.py` snapshots, `skills.write_approval` staging). A/B routing of two skill variants and comparing their scored outcomes is new but should be **deferred** (needs the P2-2 join landed first to have a comparison metric). | **L (defer)** |
| **P2-6c Reward signals from real feedback** (explicit thumbs up/down to stored rating) | **GENUINE-GAP-BUILD (small)** | `outcomes/signals.py` infers implicit affirmation/correction only; there is no explicit capture path. Add a feedback hook -> write `user_rating` onto the matching trace (Langfuse `create_score`) and the `turn_outcomes` row. | **S-M** |
| **P2-6d Light user knowledge-graph** | **ALREADY-EXISTS-REUSE (see 3b)** | Honcho peer card + holographic entities cover it. Micro-supplement only. | **S** |
| **P2-6e Real context-compression path** | **OUT OF PART-2 SCOPE (belongs to Part 1 retention, item #9)** | This is the raw to summaries to patterns to lessons path already queued as Part 1 OPEN #9; not an observability item. | n/a here |
| **SFT/DPO/RLHF training pipeline** | **OUT OF SCOPE (flag, do not build)** | Confirmed absent everywhere. Collect clean scored traces; stop. | n/a |

Cross-cutting GENUINE gap shared by P2-1/2/3/6a: **`turn_outcomes` keys only on `session_id`+`turn`** (verified: `store.py:20-30`, grep for `agent_id|subagent|delegate` in `plugins/outcomes/*.py` returns ZERO). So you cannot answer "which agent/persona produces good runs". The fix is one additive nullable `agent_id` (and optional `subagent_id`/`role`) column on `turn_outcomes`, threaded through `engine.py:141-183`. This is the smallest correct change that unlocks cross-agent observability. **S-M.**

---

## 3b. Honcho user-representation verdict

**Decision: do NOT build a separate user knowledge-graph. Reuse Honcho for the user model and the holographic entities table for typed structure. One micro-supplement only.**

- Honcho already provides a structured-enough user representation: `honcho_profile` returns a peer card = "a curated list of key facts (name, role, preferences, communication style, patterns)" (`plugins/memory/honcho/__init__.py:37-61`), `honcho_conclude` persists self-healing conclusions (:155-181), plus representation/dialectic. Preferences, role, and style are covered as atomic NL facts.
- What Honcho does NOT give first-class is a typed entity-relationship graph (company -> projects -> clients with typed edges). But the repo already ships that substrate: the holographic plugin's `entities(entity_id, name, entity_type, aliases)` + `fact_entities` join + categorized `facts` (`plugins/memory/holographic/store.py:20-44`) with probe/related/reason actions.
- The ONLY genuine micro-gap: `entity_type` defaults to free-text `'unknown'` (verified `store.py:36`), so company/client/project/person typing is unenforced. **Supplement = a light controlled vocabulary on the EXISTING column, not a new graph DB.** Effort **S**. This is reuse, not rebuild.

---

## 4. Phased, reversible build plan (working slice first)

Principles enforced throughout: reuse Curator/idle-fork/rollback; never fork a parallel system; dreaming PROPOSES to `PROPOSALS.md` and the existing HMAC review queue and **never auto-applies**; every step independently testable with real evidence; commit after each wave (a shared-tree clobber already ate one build — see RECOVERY-NOTE.md). Defaults OFF; the memory mission enables via `config.yaml`, never new `HERMES_*` env vars.

**Slice 0 (working slice, S) — agent dimension on the evaluator.**
Add nullable `agent_id`/`subagent_id`/`role` columns to `turn_outcomes` (`store.py`) via the same additive `ALTER TABLE ... ADD COLUMN` pattern already at `store.py:52-54`; thread an optional `agent_id` through `engine.py` finalize/stage/_score_and_record (default None = unchanged behavior). 
Evidence: a unit test that records two turns under different agent_ids and reads them back grouped; baseline outcomes tests still green; existing `recent_turn_scores`/`recent_session_scores` callers (dreaming `outcome_link.py`, `self_evolution/cycle.py`) unaffected (read-contract stable).

**Slice 1 (S) — cross-agent trace linkage.**
Add `subagent_start`/`subagent_stop` to the langfuse `register()` (:1128) and `plugin.yaml` hooks list; parent the child trace using the parent/child IDs already emitted (`delegate_tool.py:1428-1440`, :2658-2695) and Langfuse trace-context. Fail-open preserved. 
Evidence: enable the plugin against a local/self-hosted Langfuse (or a mock client), run one `delegate` call, assert the child generation nests under the parent trace; `tests/plugins/test_langfuse_plugin.py` extended and green.

**Slice 2 (M) — outcome-to-trace score bridge (the core join, P2-2).**
New small consumer (edge plugin or a method on the outcomes plugin) that, on `on_session_end` or in `run_cycle`, reads the scored `turn_outcomes` rows and calls `client.create_score` on the trace minted with the matching `create_trace_id(seed="{session}::{task}")` (:606). No new scorer. 
Evidence: a test that records a turn_score, runs the bridge against a mock Langfuse, asserts a score with the right value lands on the right trace_id; the local SQLite remains the source of truth (bridge is push-only, fail-open).

**Slice 3 (M) — skill-outcome attribution + read-only health (P2-3, P2-6a).**
At the existing use-record join points (`skill_run_tool.py:181-188`, `skills_tool.py:1606-1628`) attribute the run's turn_score to the skill(s) used; write additive `success_rate`/`avg_latency`/`cost_per_run`/`user_rating` rollup into `_empty_record` (`skill_usage.py:460-473`); surface it in the existing review render (`curator.py:1419-1423`) as a signal to the human reviewer. **Never wire it to auto-prune** (honor `curator.py:391-394`). 
Evidence: test that a 5+ call scored run updates the skill's success_rate; curator review output shows the column; `apply_automatic_transitions` behavior unchanged (no new auto-archival path).

**Slice 4 (M) — reflection PROPOSAL pass on the idle fork (P2-4).**
Register a reflection pass on the existing `on_session_start`/`on_session_end` -> `maybe_run_in_background` seam (or call from `run_dream_cycle`); it reads `recent_session_scores` + low-scoring traces and writes a human-readable proposal to `PROPOSALS.md` and the existing HMAC review queue (`plugins/dreaming/review.py`). It **never** edits skills/memory directly. 
Evidence: test that a low-score run produces a queued proposal (chain verifies via `verify_chain`), nothing is auto-applied, `/dream review` shows it; runs in a daemon thread, debounced, fail-soft (mirrors `runner.py:323-339`).

**Slice 5 (S-M) — explicit feedback capture (P2-6c) + utility view (P2-5) + Honcho entity_type vocab (3b).**
Feedback hook -> `user_rating` onto trace + `turn_outcomes`; a read-only "utility = used x helpful" rollup over outcomes + skill sidecar + Part 1 promotion band; light controlled `entity_type` vocabulary on the holographic entities table. 
Evidence: thumbs-down test writes a rating to both stores; utility view returns expected ranking; entity typed as `company` is queryable.

**Deferred (L):** skill A/B routing (P2-6b) — needs Slice 2's comparison metric first.

Reversibility: every slice is additive (nullable columns, opt-in hooks, default-OFF config, push-only bridge). Skill/memory changes route through the existing snapshot/rollback/review-queue substrate. No destructive migration; no core tool added (capability lives in the existing plugins + sidecar + CLI per the Footprint Ladder).

---

## 5. OUT OF SCOPE (explicit)

The autonomous fine-tuning dataset and any **SFT / DPO / RLHF training pipeline** are OUT OF SCOPE. Confirmed absent from the repo (no training code anywhere) and must not be added. The scored traces produced by this plan are an observability/eval substrate only; `turn_score` is consumed solely by the local dreaming/self-evolution skill loop, never a weight-update path. **Collect clean traces, stop there.**

---

## 6. Open questions for the user

1. **Langfuse enablement story.** The langfuse plugin is opt-in and currently absent from `plugins.enabled` (dormant). For the memory mission, do we enable outcome-aware tracing by default via `config.yaml` (self-hosted Langfuse), or keep it strictly opt-in per `AGENTS.md`'s no-un-gated-outbound-telemetry rule? This also affects the platform's many VMs (single-tenant, env-keyed). [Blocks Slices 1-2 going default-on.]
2. **Local-only score store vs Langfuse.** Do you want the cross-agent eval rollup to live (a) only locally as a thin view over `turn_outcomes` + the skill sidecar (no outbound dep, works offline), or (b) in Langfuse as the aggregation plane once scores land on traces? Slice 0/3 give (a) for free; Slice 2 adds (b). Recommend shipping (a) first, (b) opt-in.
3. **Observer schema-version drift.** Code says `hermes.observer.v1` (`middleware.py:17`); docs say `opencomputer.observer.v1` (`docs/observability/README.md:42`). Which is canonical post-rebrand? (Trivial fix, but it is the contract version string — confirm before touching.)
4. **`judge_enabled` cost posture.** Live config already has the aux-LLM judge ON (`~/.hermes/config.yaml:694`). The cross-agent bridge will multiply judged turns across delegated subagents. Confirm the token budget, or scope the judge to the nightly `rejudge_recent` batch only (composite stays free per turn).
5. **Skill-health visibility.** Should the new `success_rate`/`cost`/`rating` skill columns surface only in the Curator review report (operator-facing), or also in the frontend Memory tab? Default plan keeps it report-only and read-only (never an auto-prune trigger).
