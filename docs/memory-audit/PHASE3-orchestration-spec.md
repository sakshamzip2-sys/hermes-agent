# Memory Mission — LOCKED Orchestration Spec (Requirement #12)

Status: LOCKED. This is the binding design for Requirement #12 (both senses). It supersedes the three component drafts and folds in every surviving mitigation from the three red-team verdicts (RMS = wounded, BEOH = killed, two-layer plane = wounded). The killed and wounded designs are accepted ONLY with their required mitigations made load-bearing parts of the spec below. No em dashes anywhere (house rule).

Naming: the two layers ship as v2 edge plugins. Layer A (runtime) = `plugins/memory_supervisor/` (the Runtime Memory Supervisor, RMS). Layer B (build-time) = `plugins/oc_buildloop/` (the Build-Execution Orchestrator, BEOH). One shared discipline, two separate state DBs, zero new core tools.

---

## 1. The two-layer orchestration model

There are two orchestrators with the same shape and opposite lifetimes. They share design DNA (durable SQLite state, reconcile-on-read liveness, per-target circuit breakers, single-writer lease, fail-open reads / fail-closed writes) but are deliberately decoupled so a fault in one cannot reach the other.

| | Layer A: Runtime Memory Supervisor (RMS) | Layer B: Build-Execution Orchestrator (BEOH) |
|---|---|---|
| Requirement | #12a | #12b |
| Lifetime | continuous, lives inside the running gateway | one-shot, drives Phases 4 to 6 then exits |
| Supervises | the 4 memory stores + the background memory jobs (extract/write, compaction/retention, promotion, recall-eval) | the 6 build workstreams + their fix-swarms + the completeness critic |
| Goal | the agent never silently degrades when a store is down; recall fails open, writes fail closed | the memory mission gets built and proven green, self-healing through failures, never looping forever |
| State DB | `~/.hermes/mem_supervisor.db` (WAL) | `<repo>/.oc_buildloop/buildloop.db` + the existing `oc_flow.db` |
| Engine | new daemon thread next to `gateway/memory_monitor.py` + 3 additive hooks | new flow on the existing `oc_flow` engine + a conductor module |

How they relate: BEOH is what BUILDS the memory layer (the merge layer, the write path, the RMS itself as workstream WS3, promotion, eval, proof). RMS is what RUNS once it is built. They meet at exactly one seam: BEOH workstream WS3 produces `plugins/memory_supervisor/`, and BEOH's verify gates exercise RMS in fault-injection mode (chaos tests, see section 3). They share NO runtime process, NO state DB, and NO in-memory object. The red-team flagged "should they share a probe/breaker lib" as an open question; the locked answer is DECOUPLED NOW. A shared `oc_resilience` helper lib (lease, breaker, reconcile) MAY be extracted later only after both ship and only behind tests; premature coupling is forbidden because a bug in a shared lib would cross the one boundary we are paying to keep separate.

The non-negotiable invariant binding both layers: **the agent turn never blocks on, and never fails because of, either orchestrator.** Every hook is soft-imported; every loop tick and every external call is individually wrapped; absence of the plugin equals today's exact behavior.

---

## 2. Runtime Memory Supervisor (RMS) — final design

### 2.1 What it fixes (the verified Phase-1 condition)

Today `is_available()` is config-only (it "should not make network calls"), so Honcho and GBrain being down (both DOWN today) produces ZERO signal: `prefetch_all` and `sync_all` swallow failures into `logger.warning`/`logger.debug` with no state, the recall fan-out still dispatches to the dead store and eats its full client timeout (the aggregator uses an 8s `_TIMEOUT`), and a failed external write VANISHES. RMS converts that silent drop into a visible breaker trip, a durable retry queue, and an agent-visible degrade note.

### 2.2 Control loop (final, watchdogged)

RMS hosts a SECOND daemon thread alongside the existing RSS monitor, started from the same gateway call site as `start_memory_monitoring`, using the same `daemon=True` + `_lock` + idempotent-start discipline. Each tick (default 10s) is wrapped in try/except so the loop never dies (mirrors `_monitor_loop` at memory_monitor.py:131). Per tick, ONLY the lease-holder (section 2.4) does mutating work; non-holders do read-only health publication.

Per tick (leader):
1. **Heartbeat first.** Write `supervisor_status.last_tick_at = now` BEFORE anything else. This is the watchdog signal (section 2.3).
2. **Probe** each store via its breaker, each probe under a HARD wall-clock deadline (section 2.3), not just the httpx 2s timeout. HALF_OPEN stores get their single gated probe here; CLOSED stores get a cheap liveness probe every Nth tick.
3. **Reconcile jobs.** Demote any job row in a live state whose owning process is provably dead (section 2.6 identity check, not bare `os.kill`) or whose lease expired; re-enqueue subject to retry budget.
4. **Drain the write queue** for each store whose breaker is CLOSED/HALF_OPEN, in order, on a DEDICATED drainer worker (section 2.5), rate-limited by token bucket, never on the turn-sync worker.
5. **Retry ladder.** Re-enqueue `failed` jobs whose backoff (capped exp + full jitter) has elapsed and whose failure class is transient (section 2.7); send permanent-class failures straight to `dead_letter` without flapping the breaker.
6. **Schedule** retention/compaction and recall-eval as `jobs` (leases, not inline), idempotent by `UNIQUE(job_type, period_key)`.
7. **Publish health** to `store_health`/`supervisor_status` for the aggregator AND emit an agent-visible note when state changed (section 2.9).

### 2.3 Watchdog-of-the-watchdog (RMS-required-mitigation, all three verdicts)

`is_alive()` is NOT liveness. The existing RSS monitor (a separate thread) gains one assertion on its own timer: if `supervisor_status.last_tick_at` is older than `3 * tick_interval`, it (a) logs LOUD at WARNING, (b) restarts the RMS loop thread, and (c) flips ALL breakers to a defined `supervisor_degraded` state until the loop proves a fresh heartbeat. The gateway start path must verify `start_memory_supervisor()` actually returned True and re-arm on False (the start hook can silently return False, exactly as `start_memory_monitoring` does when RSS is unreadable). Every probe runs inside a `concurrent.futures` future with a hard `result(timeout=probe_deadline)`; a socket-level stall that ignores the httpx timeout cannot wedge the single loop thread, because the loop abandons the future and counts it as a probe failure. On read, any `store_health` row whose `last_probe_at` exceeds `3 * tick_interval` is treated as `unknown -> re-probe`, continuously, not only at cold start.

### 2.4 Single-writer lease (RMS-required-mitigation: split-brain)

CLAUDE.md documents a real dual-gateway local stack (`:8642` daemon + launchd daemon) sharing one `~/.hermes/mem_supervisor.db`. WAL gives concurrent readers but exactly one writer. The leader lease (the open-question the drafts punted) is BUILT, not deferred: a `supervisor_leader(id PK, holder_token, lease_until)` row plus an OS advisory lock (`fcntl.flock` on `mem_supervisor.db.lock`). `holder_token` = boot-id + pid + random nonce, so PID reuse cannot impersonate a dead holder. Only the lease-holder drains the write queue and runs scheduled jobs; every other hermes process probes and serves health read-only. The lease is renewed each tick and expires after `3 * tick_interval`; a non-holder takes it only after expiry AND after confirming the prior holder is not alive (section 2.6).

### 2.5 Durable fail-closed write queue (final)

`mem_supervisor.db` (WAL, `busy_timeout=30000`, thread-local conn), modeled on the oc_flow/oc_agents pattern. Tables: `write_queue`, `jobs` (`UNIQUE(job_type, period_key)`), `store_health`, `supervisor_status`, `supervisor_leader`, `recall_trace`.

Write path, fail-CLOSED, executed at the `sync_all`/`on_memory_write`/`on_delegation` seam (where today the write is logged and dropped):
1. **Backup gate (Req #2), decoupled.** On the first mutating write of a session to a durable store, take a timestamped backup under `~/.hermes/backups/` and record `backup_id`. CRITICAL FIX (verdict deadlock): the backup precondition applies ONLY to remote/external durable writes (Honcho/GBrain), NEVER blocks local-only writes (FTS5/Markdown already commit synchronously), and the backup dir lives on a different filesystem/quota than the queue DB so a full queue can never starve the backup. A backup-dir failure surfaces as a health alarm and PARKS the affected store; it does not stall the whole write path.
2. **Injection scan (Req #11).** `scan_for_threats(content, scope="strict")` (the same `tools/threat_patterns.py` scanner the read fence uses). On a hit: `scanned=blocked`, write to NO store, surface the block count in health. This closes the write-side gap (today only recall is fenced).
3. **Claim/ack with crash-safe idempotency (RMS-required-mitigation: double-apply).** Each row carries a stable `dedup_key = sha256(store_id | op | normalized_content)` where the hash EXCLUDES volatile metadata/timestamps. The drainer does claim (lease the row) -> provider call -> ack as distinct steps. Because a crash can land between provider-commit and local-ack, the `dedup_key` is sent to the store as a client-supplied idempotency id (Honcho message id / GBrain upsert key) so a re-drained row is a true server-side no-op. If a store cannot accept a client-supplied id, its writes are marked `at_least_once` EXPLICITLY and deduped on read-back before replay. The draft's "exactly-once because dedup_key" claim is corrected to "exactly-once where the store honors the idempotency id, at-least-once-with-read-dedup otherwise."
4. **Bounded queue + backpressure (RMS-required-mitigation: unbounded growth).** Per-store `max_queue_depth` and a disk-space floor check. Over the cap, oldest rows evict to `dead_letter` (never silently dropped). With a store down for days, the queue cannot exhaust disk and deadlock the backup gate.
5. If the breaker is CLOSED, attempt inline and mark `done`; if OPEN or the inline attempt fails, the row stays `pending` and the drainer flushes it on recovery. This replaces the silent drop: a down target means a durably queued, retried write, never a lost one.

### 2.6 Job supervision (backoff + jitter + dead-letter + correct death detection)

Lease/heartbeat per job row (`lease_until`, owner identity). Reconcile borrows the oc_agents `reconcile_liveness` pattern but FIXES its two holes (RMS-required-mitigation):
- It does NOT trust `os.kill(pid, 0)` alone (PID reuse resurrects dead work; `PermissionError` is wrongly read as "alive"). Each worker writes a `start_token` (boot-id + pid + nonce) and touches `last_progress_ts`. A row is DEAD if its `start_token` cannot be confirmed OR its `last_progress_ts` exceeds a wall-clock cap, independent of pid liveness. Cross-host/restart rows whose owning identity cannot be confirmed are treated as DEAD-needs-redispatch, never "alive."

Retry ladder: `failed` jobs with `attempts < max_attempts` (default 5) re-enqueue when `next_attempt_at` (capped exp backoff + FULL jitter) elapses; `>= max` move to `dead_letter` with a reason. Dead-letter rows increment a counter surfaced in health and emit an agent-visible note (section 2.9). Idempotency makes re-running safe (`dedup_key` / `UNIQUE(job_type, period_key)`).

### 2.7 Per-store circuit breaker state machine (final, debounced, failure-classified)

One breaker per store, mirrored to `store_health` for resumability. States CLOSED, OPEN, HALF_OPEN, plus the watchdog `supervisor_degraded`.

- CLOSED -> OPEN: requires **K consecutive probe failures** (default 3) AND a minimum probe interval, NOT a single blip (RMS-required-mitigation: false-positive trip). A timeout (no status code) requires CORROBORATION (a probe failure AND a real recall failure) before it fully removes a store from recall, so local CPU/DNS pressure from elsewhere cannot disable a healthy store.
- OPEN: recall/write calls short-circuit instantly (no network, no per-turn timeout). Cooldown is exp backoff (default 30s) up to a cap (default 300s/900s configurable) with +/-20% jitter so multiple stores never stampede recovery. After cooldown -> HALF_OPEN.
- HALF_OPEN -> CLOSED: requires **M consecutive successes** (default 2), not a single success, with hysteresis so a flapping store cannot oscillate every cycle (RMS-required-mitigation: flapping churn). A single HALF_OPEN failure -> back to OPEN, bump cooldown.
- **Failure classification (RMS-required-mitigation: OpenRouter-402 poison).** A status-code sniff splits permanent (402/401/403/400) from transient (429/5xx/timeout/connection). A 402 `insufficient_credits` does NOT flap the breaker and does NOT burn the retry ladder: the store enters a distinct `credits_exhausted` health state, keeps serving keyword/FTS5 recall (which needs no embeddings) while disabling the rerank/embedding leg (lexical-fusion fallback over session FTS5 + holographic FTS5), and parks embedding-dependent writes (they wait, they do not fail). It is cleared only by an operator topping up credits, surfaced via a one-time `PushNotification`. HALF_OPEN for a credits-dead store uses a cheap signal that distinguishes credits-dead from network-dead, never a `/health` that false-re-closes (the draft's Q4 hole).
- Slow-but-up: latency over `slow_budget_ms` is a HALF-failure (warn, keep serving), never a full trip, so a degraded-but-healthy store is never wrongly disabled.

### 2.8 The exact v2 seams

NEW (all edge, plugin-resident): `plugins/memory_supervisor/{__init__.py, plugin.yaml, registry.py, probes.py, breaker.py, wal.py, lease.py, control_loop.py, scheduler.py}` + `~/.hermes/mem_supervisor.db` (override `HERMES_MEM_SUPERVISOR_DB`), modeled on the oc_flow/oc_agents WAL + thread-local + reconcile pattern.

EXISTING files touched (additive, soft-guarded so absence = no-op):
1. `agent/memory_manager.py` — wrap the write fan-out at `sync_all` (~line 570-592), `on_memory_write` (~854-871), and `on_delegation` (~873-882, the promotion seam) to route through `enqueue_write`; have `prefetch_all` (~483-488) consult `breaker.is_open(store)` to skip OPEN stores and emit a `recall_trace`. Pure wrapper insertions; if the plugin is absent the calls pass through unchanged.
2. `gateway/memory_monitor.py` — the gateway start call also starts `memory_supervisor.start()` and verifies the return; `stop_memory_monitoring` mirrors the stop; the RSS monitor hosts the watchdog assertion (section 2.3).
3. `gateway/platforms/memory_aggregator.py:build_memory_payload` (line 466) — add a 4th concurrent `supervisor` section reading `store_health`/`supervisor_status`, fail-soft exactly like the existing local/honcho/gbrain planes. The `/api/memory` route needs zero change. New `GET /api/memory/health` is a thin read over the same rows.
4. REUSE `tools/threat_patterns.py:scan_for_threats` for the write-side injection gate.

EDGE-vs-CORE: unambiguously EDGE. New plugin + thin additive wrappers. No new core tool ships on every API call; the `memory.provider` single-provider write contract is untouched; the turn never gains a hard dependency on the supervisor.

### 2.9 Make the degrade agent-visible (RMS-required-mitigation)

The model never reads the Memory tab, so `/api/memory` alone reintroduces silent degradation. A tripped/parked store, a non-empty `dead_letter`, or `credits_exhausted` emits a signal on a path the model or operator actually sees: a turn-level system note (a one-line "memory degraded: Honcho OPEN, 3 writes queued" injected into the turn context via the existing memory-context-block seam) AND a LOUD log AND the `/api/memory` note. This ties Req #4 observability to the "degrade-with-a-visible-note" mandate. The `recall_trace` records which stores were queried, which were breaker-skipped, per-store hits/scores, fused order, and latency, so a wrongly-disabled store is visibly different from a truly-down one.

### 2.10 Config knobs + defaults

```
memory_supervisor.enabled                         false   # ships dark until chaos suite passes
memory_supervisor.tick_interval_s                 10
memory_supervisor.breaker.fail_threshold          3       # K consecutive probe fails to OPEN
memory_supervisor.breaker.recover_successes       2       # M consecutive to CLOSE (hysteresis)
memory_supervisor.breaker.cooldown_s              30
memory_supervisor.breaker.cooldown_max_s          300
memory_supervisor.breaker.timeout_needs_corrob    true    # timeout-class needs probe+recall fail
memory_supervisor.probe.timeout_s                 2
memory_supervisor.probe.hard_deadline_s           5       # wall-clock future deadline
memory_supervisor.probe.slow_budget_ms            <store-tuned>
memory_supervisor.jobs.max_attempts               5
memory_supervisor.jobs.backoff_base_s             2
memory_supervisor.jobs.backoff_jitter_frac        0.2     # full jitter
memory_supervisor.jobs.lease_timeout_s            <per job_type>
memory_supervisor.write_queue.fail_closed         true
memory_supervisor.write_queue.max_depth_per_store 5000
memory_supervisor.write_queue.disk_floor_mb       512
memory_supervisor.credits_exhausted_mode          soft    # soft-degrade-to-lexical | hard-trip
memory_supervisor.notify                          on-state-change
memory_supervisor.schedules.retention_cron / compaction_cron / eval_cron
memory_supervisor.injection_scan.scope            strict
memory_supervisor.backup.dir                      ~/.hermes/backups
memory_supervisor.backup.require_before_remote_write  true   # remote only, never local
memory_supervisor.stores.<id>.criticality         critical|high|optional
memory_supervisor.lease.enabled                   true    # single-writer; not optional
```

### 2.11 Proof it never cascade-fails the agent

Recall fails OPEN: `prefetch_all` asks breakers which stores are live and fans out only to CLOSED/HALF_OPEN stores; a missing/broken/absent plugin degrades to today's exact behavior (fan out to all providers, swallow failures). The turn proceeds on whatever planes survive. A dead store is SKIPPED, never awaited. Writes fail CLOSED: the write is journaled to the WAL queue BEFORE the provider call and marked done only on ack; a down target queues durably and replays on recovery; a poisoned payload is blocked; an un-backed remote write is refused-and-queued, never lost. The supervisor's own death is caught (soft-import + per-tick try/except + watchdog restart), and on restart it reattaches to the WAL DB and resumes draining. No single failure path lets the supervisor block or fail a turn.

---

## 3. Build-Execution Orchestrator (BEOH) — final self-healing harness

The red-team KILLED the BEOH draft because three of its load-bearing primitives did not exist on the actual `oc_flow` engine: an enforceable per-leaf timeout (`parallel()` blocks on `f.result()` with no timeout and Python cannot kill a thread), a run lock with pid reconciliation (resume trusts `status='running'` blindly), and the entire conductor (`merge_worktree`, `assert_disjoint`, `run_gate`, etc. are unimplemented). The locked design accepts BEOH ONLY with these four BLOCKER mitigations built and proven with `OC_FLOW_FAKE_AGENT=1` BEFORE any sign-off. The harness is unbuildable as drafted; it is buildable as specified here.

### 3.1 Required engine changes before the harness runs (the four BLOCKERS)

1. **Real cancellable leaf.** `oc_flow` runs each `agent()` as a child PROCESS (subprocess/ProcessPoolExecutor) so the OS can SIGKILL a hung leaf, AND the `parallel()` barrier gains `f.result(timeout=N)`. A cooperative `AIAgent.interrupt()` is sent first (as `delegate_tool` does); if the leaf does not yield, the process is killed and its worktree is abandoned, never reused. Until a hung child can actually be reclaimed, the harness does not ship.
2. **Single-writer run lock + pid reconcile.** On run/resume, take an OS file lock (`flock` on `<root>/.oc_buildloop/<runId>.lock`) and check the recorded pid identity (boot-id + pid + nonce, not bare pid). Refuse a second live writer for the same `runId`; on resume, reconcile any `status='running'` row whose owner is provably dead to `failed/incomplete` BEFORE loading the resume cache. This closes the `ON CONFLICT DO UPDATE` silent-overwrite split-brain.
3. **Global model-availability circuit breaker.** A 402/401 from the shared model endpoint trips ONE global breaker that HARD-STOPS all dispatch (workstreams, fix-swarms, critic) and escalates ONCE via `PushNotification`, BEFORE fanning out N agents into a guaranteed-402 thundering herd. Only 429/5xx/network get the jittered backoff, globally token-bucketed on recovery. This is distinct from RMS's store breakers; BEOH owns its own provider breaker (the draft's Q3 is resolved: own, do not merely inherit).
4. **Build the conductor and prove it offline.** `conductor.py` implements `assert_disjoint`, `backup_memory_stores` (Req #2 hard-stop on failure), `run_gate` (distinguishing `kind='test'` from `kind='infra'`), `made_progress`, and a real integration-branch merge (FF-only, conflict -> needs_human) that is idempotent (skip-if-already-merged keyed on the WS worktree commit sha, because the merge lives OUTSIDE the content-addressed cache). All proven under `OC_FLOW_FAKE_AGENT=1` before design sign-off.

### 3.2 The six workstreams (file-ownership-disjoint)

WS1 merge-layer -> NEW `agent/memory_merge.py` + `agent/recall_trace.py`. WS2 write-path -> EDIT `tools/memory_tool.py` + NEW `agent/memory_policy.py` (Req #8). WS3 supervisor -> NEW `plugins/memory_supervisor/` (imports `gateway/memory_monitor.py` + `memory_aggregator.py` read-only, never edits them). WS4 promotion -> NEW `agent/memory_promotion.py` (Req #10). WS5 eval -> NEW `docs/memory-audit/eval/`. WS6 proof -> NEW `docs/memory-audit/proof/prove_memory.sh`. A literal `OWNERSHIP` dict maps WS -> owned-glob-set; `assert_disjoint(OWNERSHIP)` RAISES before any agent spawns (collision guard #1, static). Each WS agent runs `worktree=True` in its own `.worktrees/<id>` (collision guard #2, physical).

### 3.3 Top + sub orchestrators (pseudocode in oc_flow primitives)

```
# LEVEL 0 — TOP orchestrator = the flow body
META = {name: 'memory-build-harness', phases: [...]}
cfg   = args or {}
N     = cfg.get('max_fix_iters', 3)
BUDGET = Budget(total=cfg.get('max_total_agents', 60),
                per_ws=cfg.get('max_fix_agents_per_ws', 8),
                wall_clock_s=cfg.get('max_run_wall_s', 14400),   # global termination budget
                critic_rounds=cfg.get('max_critic_rounds', 2))

acquire_run_lock(runId)                       # BLOCKER 2: single-writer + pid reconcile
assert_disjoint(OWNERSHIP)                    # collision guard #1 (static, raises)
phase('preflight')
if not conductor.backup_memory_stores().ok:   # Req #2 — HARD STOP, nothing mutated
    return hard_stop('backup failed')
base = conductor.git_create_integration_branch()   # main untouched

phase('build')                                # PHASE A — parallel build, one thunk per WS
ws_results = parallel([ (lambda w=w: build_suborch(w, BUDGET)) for w in WORKSTREAMS ])

phase('integrate-verify')                     # PHASE B — domain TEST + VERIFY sub-orchestrators
conductor.merge_all_ready(ws_results, base)   # FF-only, idempotent skip-if-merged, conflict->needs_human
integ = test_suborch('baseline+new')
while not integ.ok and BUDGET.left() and integ.iter < N and made_progress(integ):
    cross = verify_suborch('integration', integ.failures)   # cross-WS breakage diagnosis (read-only agent)
    fix_batch(cross.subfixes)                                # disjoint -> parallel; shared-file -> serialized
    integ = test_suborch('baseline+new')

phase('completeness-critic')                  # PHASE C — critic that can launch MORE swarms
crit = agent(critic_prompt(REQUIREMENTS, ws_results, integ), label='critic', toolsets=read_only)
for gap in crit.gaps:
    if gap.live_dependency_down:              # Honcho/GBrain down today -> do NOT burn budget
        open_questions.append(gap); continue
    if not conductor.gap_reduces_objective_metric(gap):  # independent, code-measured rejection gate
        continue
    if BUDGET.critic_rounds_left() and BUDGET.left():
        build_suborch(gap.as_disjoint_workstream(), BUDGET)   # NEW disjoint files only
integ = test_suborch('baseline+new')

phase('proof'); proof = run_gate_proof()      # PHASE D — prove_memory.sh + eval, real stdout (Req #5/6/7)
result({runId, ws_results, integ, crit, proof, open_questions})
```

```
# LEVEL 1 — domain sub-orchestrator: the per-workstream self-healing loop
def run_workstream(ws, budget):
    iters = 0; infra_iters = 0; last_fail_hash = None
    value = with_leaf_guard(lambda: agent(impl_prompt(ws), label=ws+':impl',
                                          worktree=True, cwd=repo_root, schema=IMPL_RESULT))
    if value is None or not validate(value, IMPL_RESULT):     # null/dead-agent handling
        value = retry_once_fresh_worktree(ws) or mark_needs_human(ws)
    while True:
        gate = run_gate(ws, value.worktree)                   # deterministic shell; ground truth
        if gate.ok:
            return conductor.merge_worktree(ws.branch)        # FF or conflict->needs_human
        if gate.kind == 'infra':                              # collection crash / ruff missing / git lock
            infra_iters += 1
            if infra_iters > MAX_INFRA_ITERS:                 # hard ceiling INCLUDING infra
                return quarantine(ws, 'flapping infra')       # do NOT respawn repair forever
            repair_environment_once(); continue               # not counted as a fix iter, but capped
        fh = failure_hash(gate.failures)
        if fh == last_fail_hash:                              # no-progress guard (identical fails)
            return mark_needs_human(ws, gate.failures)        # break immediately, do not burn N
        last_fail_hash = fh
        iters += 1
        if iters >= N or not budget.ws_left(ws):
            return mark_needs_human(ws, gate.failures)
        diag = agent(diagnose_prompt(ws, gate.failures, gate.log_tail),
                     label=ws+':diagnose', schema=DIAGNOSIS, toolsets=read_only)  # read-only
        if len(diag.independent_subfixes) >= 2 and disjoint(diag.independent_subfixes):
            fixes = parallel([ (lambda sf=sf: with_leaf_guard(
                        lambda: agent(fix_prompt(sf), worktree=True))) for sf in diag.independent_subfixes ])
        else:
            fixes = with_leaf_guard(lambda: agent(fix_prompt(diag), worktree=True))
        value = pick_best_or_rollback(fixes, last_green_snapshot(ws))   # regression -> roll back to last green
```

```
# LEVEL 1 — leaf guard wrapping every agent() (BLOCKER 1 + 402 + null handling)
def with_leaf_guard(thunk):
    try:
        return run_in_child_process_with_timeout(thunk, wall=cfg.agent_wall_timeout_s)
    except ProviderHardError as e:        # 402/401 -> global breaker, no retry
        trip_global_provider_breaker(e); raise HardStop
    except (TimeoutError, ProviderTransientError):
        return retry_once_backoff(thunk, [2, 8])   # transient only, fresh worktree
```

### 3.4 Stuck / null / dead handling

- **Hung leaf:** child-process timeout (BLOCKER 1) SIGKILLs it, abandons the worktree, retries the same spec ONCE in a fresh worktree, then `needs_human`. `parallel()` isolates siblings; one hang never wedges the batch.
- **Detached-worker hang (the impossible-detection hole):** if a workstream is dispatched as a detached subprocess rather than an in-process child, hang detection uses a durable wall-clock `last_progress_ts` on the bg-session row (touched each turn/tool-boundary) plus the `start_token` identity, NOT `delegate_tool.get_activity_summary()` (an in-process reference that does not exist for a detached pid). A live-but-idle pid past the wall-clock cap is failed.
- **Null / non-conforming result:** `parallel()` resolves a crashed thunk to None; a bad schema fails `validate()`. Treated as a recoverable failure: retry once (fresh worktree, backoff), then record `status=dead` and do NOT propagate None into downstream logic (no NoneType cascade). The run continues.
- **Infra gate error vs test failure:** `run_gate` returns `kind='infra'` for collection crashes / missing tools / git failures; these do NOT count as fix iterations (editing code never converges them) but ARE capped by a separate hard `MAX_INFRA_ITERS` and a flapping-signature quarantine, so a non-deterministic infra fault cannot spin the loop indefinitely (the draft's `made_progress`-defeated-by-changing-signature hole).

### 3.5 Bounded retries + infinite-loop + collision guards

Three retry layers, all bounded: per-fix-iteration `N=3`; per-agent transient retry once with [2s, 8s] backoff (same worktree); dead/null retry once. PLUS a GLOBAL run-level termination budget (`max_total_agents=60`, `max_run_wall_s`, `max_critic_rounds=2`) above the per-workstream ceilings, because per-workstream ceilings do NOT compose into a global guarantee when failures are coupled across workstreams. Regression-oscillation detection: if rolling back WS-A's fix re-breaks WS-B and vice-versa, the cycle is detected and escalated rather than looped. Collision: static `assert_disjoint` pre-spawn + physical worktree isolation + FF-only merge where any out-of-scope hunk marks the WS `needs_human` and is never auto-merged, keeping main and the integration branch clean.

### 3.6 Completeness critic (independent grader, real rejection gate)

The critic is a SEPARATE reviewer agent (never the producer, per the routing policy), and its gap claims are GATED by an independent, code-measured objective metric (req-to-test mapping counts, failing-count delta) computed in `conductor`, NOT asserted by the critic itself. A critic-spawned remediation WS is rejected unless it reduces that objective count, must touch only NEW/disjoint files (a cross-cutting fix routes back to the owning WS's loop, never a new WS), and is NEVER spawned for a mechanism whose live dependency is down (Honcho/GBrain today). Such gaps are emitted as `open_questions` for a human, not burned as budget on unwinnable remediation. Bounded by `max_critic_rounds` AND the global budget.

### 3.7 Verify gates (ground truth, not model claims)

Scoped WS gate: `ruff check <owned> && python -m pytest <ws_target> -q -p no:cacheprovider`. Integration gate: pytest over the NAMED baseline fileset (`conductor.BASELINE_TESTS`) PLUS the new tests, plus ruff over owned dirs, plus typecheck. The gate asserts `new_pass_count > 0` AND `baseline_pass_count >= BASELINE_GREEN`, where `BASELINE_GREEN` is captured ONCE in PHASE 0 so a flaky deselect cannot lower the bar. RESOLVED open question: `BASELINE_GREEN` pins the curated memory-tagged fileset green-count captured live in PHASE 0 (whatever the real collected count is that session), not a hardcoded number, so the gate is correct regardless of the 280-vs-870 discrepancy. Proof gate (Req #6): `prove_memory.sh` exits 0 and prints retrieval through each mechanism. Eval gate (Req #7): recall@k/precision@k emitted; advisory on first run, a hard block once a baseline exists. A flaky gate driven by the down memory stores is quarantined (section 3.4) so green/red oscillation cannot drive an infinite loop.

### 3.8 BEOH v2 seams

EDGE. NEW `plugins/oc_flow/examples/memory_build_harness.py` (the flow) + `docs/memory-audit/build/conductor.py` (pure helpers, the only shell-out site) + `docs/memory-audit/build/test_conductor.py`. BUILDS ON (imports, never edits, AFTER the BLOCKER-1/2 engine changes land): `plugins/oc_flow/runtime.py`, `executor.py`, `db.py`, `worktrees.py`, and `tools/delegate_tool.py` (for the detached-worker wall-clock semantics). Invoked `hermes flow run plugins/oc_flow/examples/memory_build_harness.py --args JSON`; exposed read-only at `/api/buildloop`. The four engine BLOCKERS (cancellable leaf, run lock, provider breaker, plus `db.py` resume-by-call_index fix) are small, additive `oc_flow` core changes justified because the harness is unbuildable without them; they are confined to the flow engine, not the agent turn path.

### 3.9 Resume-cache correctness (BEOH-required-mitigation)

The runtime's `_load_resume_pool` collapses completed agents to `latest[sha]` and replays by sha (ignoring `call_index`), so a textually identical diagnose/critic prompt across two WS can cross-serve one agent's result to a different call, or replay a stale result against a moved integration branch. FIX: resume strictly by `(call_index AND prompt_sha)` match (as `get_cached_agent` already keys), never by a sha-keyed FIFO. Add a `script_sha` guard: if the flow source changed, invalidate downstream cache rather than replaying stale greens. The write-path WS2 crash window (side-effect committed, finish row not) is closed by never merging a WS worktree until its `finish_agent` row is committed, and on resume detecting and discarding orphaned worktrees whose call has no completed row, so a crash cannot double-apply or silently lose the WS2 memory-write mutation (Req #2/#8 hold across an orchestrator crash, not just a clean re-run).

---

## 4. Consolidated FAILURE-HANDLING MATRIX

### 4.1 Runtime Memory Supervisor (Layer A)

| Failure | Detection | Response |
|---|---|---|
| Store (Honcho/GBrain) down mid-session (Phase-1 silent-degradation) | active probe under hard deadline + K consecutive recall/write failures | breaker CLOSED->OPEN; recall short-circuits instantly (no per-turn timeout); writes durably queue (not dropped); health flips degraded with an AGENT-VISIBLE note; auto-recovery via HALF_OPEN |
| Down store comes back | after jittered cooldown -> HALF_OPEN single gated probe; needs M consecutive successes | breaker CLOSES, drains queued writes in order on the dedicated drainer, recall re-includes it; no restart |
| Loop tick HANGS (socket/DNS stall ignoring httpx timeout) | RSS-monitor watchdog: `last_tick_at` stale > 3x interval; per-probe hard future deadline | LOUD log; restart the loop thread; flip all breakers to `supervisor_degraded`; abandoned probe counts as a failure |
| Supervisor thread dies / never started / start returned False | watchdog `is_running()` + start-return check; soft-import guard at every hook | gateway re-arms start; on absence every hook degrades to today's exact behavior (recall fail-open, write best-effort inline); reattach WAL DB on restart |
| Split-brain double-drain (dual-gateway, shared DB) | leader lease + `flock`; non-holders read-only | only the lease-holder drains/schedules; PID-reuse-proof `holder_token`; eliminates duplicate external writes and SQLITE_BUSY storms |
| Write to a down store (fail-closed) | breaker OPEN at enqueue, or inline attempt throws | row stays `pending` in WAL queue (survives restart), drained on recovery; data never lost; replaces the silent `except/logger.debug` drop |
| Crash between provider-commit and local-ack (double-apply) | reconcile re-enqueues an `inflight` row whose owner is dead | `dedup_key` sent as client idempotency id -> server-side no-op; stores that cannot honor it are marked `at_least_once` + read-deduped before replay |
| OpenRouter 402 / no credits (recurring live blocker) | status-code classification (402/401/403/400 = permanent) | `credits_exhausted` state (not `down`); keep FTS5/keyword recall, disable rerank leg -> lexical fusion; park embedding-writes; one-time PushNotification; no breaker flap, no retry storm |
| Job hangs / its process dies | lease + `start_token` identity + `last_progress_ts` wall-clock cap (NOT bare `os.kill`) | demote to `failed`, re-enqueue with capped exp backoff + full jitter; idempotent so re-run is safe |
| Job exceeds retry budget | `attempts >= max_attempts` | move to `dead_letter` (never discarded), bump counter, emit agent-visible note; other stores unaffected |
| Poisoned/injection write payload | `scan_for_threats(scope=strict)` at enqueue | `scanned=blocked`, write to NO store, surface block count; closes the write-side fence gap |
| Backup not yet taken (remote write only) | backup-taken check at enqueue | take + record timestamped backup before the REMOTE write; never blocks local writes; backup-dir failure parks that store, does not stall the path |
| Queue grows unbounded (store down for days) | per-store `max_queue_depth` + disk-floor check | oldest evict to `dead_letter` (alarmed); backup DB on a separate filesystem so the queue cannot starve it |
| False-positive trip (transient blip / slow probe) | timeout-class needs corroboration (probe AND recall fail); slow = half-failure | a slow-but-healthy store stays serving (warn only); only corroborated full failures OPEN the breaker |
| Recovery thundering herd (cross-process) | single HALF_OPEN probe + token-bucket drainer + full jitter, all GATED BY THE LEASE | only the leader probes/drains; jitter de-syncs timers; no N-process pile-on on a just-recovered store |
| Delayed sub-agent write replay leaks across isolation (Req #10) | each spool row carries target profile identity | drainer routes to that profile; a test proves a delayed replay (sub-agent dead) lands in the sub-agent DB, not the orchestrator; until proven the spool does NOT cover sub-agent writes |

### 4.2 Build-Execution Orchestrator (Layer B)

| Failure | Detection | Response |
|---|---|---|
| Two WS agents edit the same file | static `assert_disjoint` pre-spawn; physical `worktree=True` isolation; merge-time conflict | refuse to start (print overlap); at merge, conflicting/out-of-scope hunk -> WS `needs_human`, not auto-merged; main + integration branch stay clean |
| Leaf agent hangs / never returns | child-process timeout via `result(timeout=N)` (BLOCKER 1); detached worker `last_progress_ts` wall-clock cap | SIGKILL + abandon worktree; retry same spec once in a fresh worktree; then `needs_human`; siblings isolated |
| Agent returns null/None or non-conforming | `parallel()` resolves crash to None; `validate(result, SCHEMA)` fails | retry once (fresh worktree, backoff); on second null `status=dead`; never propagate None downstream; run continues |
| Fix-loop makes no progress | identical sorted failure-signature hash twice | break immediately (do not burn remaining N); WS `needs_human`; surface to critic |
| Flapping infra fault (changing signature defeats no-progress) | `run_gate.kind='infra'` + separate hard `MAX_INFRA_ITERS` + flapping quarantine | repair env once, capped; after ceiling, quarantine the WS rather than respawn repair agents forever |
| Verify gate infra error (collection crash / ruff/git missing) | subprocess returncode distinguishes `kind='infra'` from `kind='test'` | one environment-repair agent + one re-run; still broken -> hard-stop that WS with a clear blocker (backups already taken) |
| OpenRouter 402 across all WS at once | global provider breaker, status-code classified | HARD-STOP all dispatch + fix-swarms + critic; escalate ONCE via PushNotification BEFORE the herd; no per-WS budget burn |
| Memory backup fails in PHASE 0 | `backup_memory_stores().ok == False` | HARD STOP entire harness before any spawn; exit non-zero; nothing mutated (Req #2/#3) |
| Resume cross-serves a cached result | resume keyed strictly by `(call_index AND prompt_sha)` + `script_sha` guard | never replay a sha-keyed FIFO across calls; stale-source greens invalidated; gate is actually re-run |
| Crash between WS2 side-effect and finish-row commit | on resume, orphaned worktree with no completed call row | discard orphan; never merge a WS until its finish row commits; no double-apply / silent loss of the memory-write change |
| Flow process dies / machine restarts | `oc_flow.db` run row + pid reconcile on `--resume` | resume by `runId`: completed WS return from content-addressed cache (zero re-spend); only incomplete/failed WS re-run; surviving worktrees reused |
| Split-brain orchestrators (two `--resume` of same runId) | OS `flock` run lock + pid-identity reconcile | refuse a second live writer; reconcile dead `running` rows before loading cache; no `ON CONFLICT` silent row loss, no duplicate paid spend |
| Fix-swarm regresses (breaks a previously-green check) | post-fix gate red, or red in a check green in the last-green snapshot | roll the fix worktree back to last-green; bump swarm-attempt counter; after `max_swarm_attempts` escalate; grader is always a SEPARATE reviewer |
| Regression oscillation across coupled WS | A's rollback re-breaks B and vice-versa (cycle detector) | detect the cycle, stop, escalate; the global run budget terminates even when per-WS ceilings would not |
| Critic hallucinates gaps / self-amplifies | independent code-measured metric gate + `max_critic_rounds` + global budget | reject any remediation WS that does not reduce the objective count; never spawn for a down-dependency mechanism (emit as open_question); stop at budget/round ceiling |

### 4.3 DECISION POINTS where a NEW swarm is launched

A new fix-swarm or remediation workstream is launched at exactly these points, each guarded:
1. **Per-WS verify gate red AND `diag` returns >= 2 independent, file-disjoint subfixes** -> parallel fix-swarm in fresh worktrees. (Single subfix or shared-file -> single fix agent, serialized.) Guard: `iters < N`, no-progress hash differs, `budget.ws_left`.
2. **Integration gate red after merge (cross-WS breakage)** -> `verify_suborch` diagnoses, `fix_batch` launches disjoint cross-fixes in parallel / shared-file fixes serialized. Guard: `BUDGET.left()`, `integ.iter < N`, `made_progress`.
3. **Completeness critic finds an under-covered requirement** -> a NEW disjoint-files remediation workstream re-enters the self-healing loop. Guard: gap reduces an independent code-measured metric, gap's live dependency is UP, `max_critic_rounds` and global budget remain.

A decision ESCALATES to a human (PAUSE that branch, persist the request, PushNotification, surface at end; reversible work continues) instead of launching a swarm when: an irreversible action is required (memory deletion/migration, Req #3); the provider is 402/credits-dead; `max_swarm_attempts`/`max_critic_rounds` is hit; a regression oscillation cycle is detected; a gap's live dependency is down (Honcho/GBrain today); or any high-stakes action (force-push, VM provision, schema migration) is implicated.

---

## 5. Integration with the rest of the memory design, and self-resumability

### 5.1 What RMS supervises across the memory architecture

- **Merge layer (Req #4):** `prefetch_all` consults breakers before fanning out, so the merge layer queries only live stores; every merge emits a `recall_trace` (query, stores queried, breaker-skipped stores, per-store hits/scores, fused order, latency). RMS is the source of truth for "which store is live," and the merge layer is the consumer.
- **Write path (Req #8):** every external write routes through `enqueue_write`, which enforces the store/not-store policy gate, redaction, and the injection scan BEFORE journaling. Local FTS5/Markdown commit synchronously and are not gated by the backup precondition; only remote durable writes are.
- **Promotion (Req #10):** `on_delegation` routes through the supervisor, which ENFORCES whatever the promotion allow-list policy decides (the supervisor does not define the policy; WS4 does). Spooled sub-agent writes carry target-profile identity so a delayed replay respects isolation. Until that isolation-preserving replay is proven by test, the spool does not cover sub-agent writes.
- **Retention/compaction (Req #9):** scheduled `jobs` connect the existing `curator:` block and the dreaming consolidation to the fact stores (calling their real entrypoints, not reimplementing eviction), keeping growth bounded with leases instead of inline blocking.
- **Eval (Req #7):** the recall-eval runs as a scheduled `job`, producing recall@k/precision@k per mechanism and for the merged layer; results land in `supervisor_status` and gate the default-on decision.

BEOH supervises the BUILD of all of the above: WS1 merge layer, WS2 write path, WS3 RMS itself, WS4 promotion, WS5 eval, WS6 proof. The mission flows top-down (BEOH builds) then the running gateway flows continuously (RMS supervises).

### 5.2 How the orchestration plane is itself resumable and idempotent

- **RMS:** all state lives in `mem_supervisor.db` (WAL), never memory-only, so a gateway restart loses nothing; it reattaches and resumes probing + draining `pending`/`inflight` rows. Idempotency is schema-enforced: `write_queue.dedup_key UNIQUE` (INSERT OR IGNORE), `jobs UNIQUE(job_type, period_key)`, and client-supplied idempotency ids propagated to the stores so replay is a true no-op (or at-least-once-with-read-dedup where a store cannot honor it). Reconcile-on-read (identity-checked, not bare pid) demotes dead `inflight` rows so a double-apply is harmless. The single-writer lease prevents concurrent gateways from double-draining. Backups are timestamped and recorded by `backup_id` so a rollback is always available (Req #2/#5).
- **BEOH:** resumable by `runId` on the content-addressed `oc_flow.db` cache, keyed strictly by `(call_index AND prompt_sha)` with a `script_sha` invalidation guard, so completed workstreams cost zero tokens on resume and only failed/incomplete WS re-run. Worktrees persist across a crash; gates are pure read-only shells safe to re-run; merges are FF-only and idempotent (skip-if-already-merged on the WS commit sha). The run lock + pid reconcile prevents split-brain resume. PHASE 0 backup-gating plus the never-merge-before-finish-row rule mean a re-run never double-mutates real memory code.

Both layers obey the binding invariant: the agent turn never blocks on, and never fails because of, either orchestrator. RMS recall fails OPEN, RMS writes fail CLOSED, BEOH isolates every leaf and bounds every loop, and neither shares a process, a state DB, or an in-memory object with the other. This spec is LOCKED; the BEOH four BLOCKERS and the RMS lease + watchdog + failure-classification + agent-visible-degrade mitigations are load-bearing and must be built and chaos-tested (dual-gateway split-brain drain, kill-9 mid-inflight, 402-storm, flapping store, hung probe, full-disk/unwritable-backup, corrupt DB) before `memory_supervisor.enabled` is flipped on for the local stack.
