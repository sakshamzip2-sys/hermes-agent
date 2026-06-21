# OC Defaults + Branding Mission — PROGRESS

Branch: `feat/oc-default-plugins-and-branding` (off `feat/agents-orchestrator-mission`, on `origin` = sakshamzip2-sys/hermes-agent fork).
Goal: ship the owner's curated setup as repo defaults so any clone/fork inherits it, and make `oc`/`opencomputer` the primary command + brand (hermes kept as silent alias). No plumbing rename (upstream-merge-safe). Stop only at the protected-branch/PR gate.

## Lifecycle gates
- Reversible work: full authority (granted by user).
- GATED: PR / merge to a protected branch (main/upstream). Do NOT without explicit go.

## Phase status

### P0 — Branch + safety — DONE
- Feature branch created off clean base (0 uncommitted). Config backups taken.

### P1 — Enable requested plugins LOCAL + GLOBAL default — DONE + PROVEN
Local (~/.hermes/config.yaml plugins.enabled): enabled the 12 requested (model providers anthropic/xai/custom/openai-codex, image/video gen openai/openai-codex/xai, langfuse, discord, irc, security-guidance) on top of the existing 12 → 24 total. Gateway restarted HEALTHY (PID rotated, HTTP 200, `50 found / 45 enabled`, zero tracebacks); chat smoke test → PONG; terminal toolset still enabled.
Global (in-repo, ships to all clones):
- `hermes_cli/plugins.py`: added `DEFAULT_ENABLED_PLUGINS` (24 keys, single source of truth); `_get_enabled_plugins()` now returns `defaults ∪ user-config` (was opt-in/None). Opt-out via `plugins.disabled`.
- `hermes_cli/plugins_cmd.py`: `cmd_list` display unions the defaults (so `oc plugins list` shows them enabled on a fresh clone) WITHOUT polluting the mutation path.
- Tests: new `tests/plugins/test_default_enabled_plugins.py` (6 tests: floor on fresh clone, union with user cfg, every default key maps to a real bundled plugin, gated standalone defaults load). Updated `tests/plugins/test_langfuse_plugin.py` (langfuse now a shipped default). Made `tests/hermes_cli/test_plugins.py::test_request_hooks_are_invokeable` hermetic.
- Regression: bisected — 2 broken tests were MY change (fixed), 3 remaining failures (photon, self_evolution x2) are PRE-EXISTING (fail on base too).
- PROOF: empty HERMES_HOME (`oc plugins list`) shows the providers/langfuse/discord/irc/security-guidance as ENABLED, no config written. 149 plugin tests green.

### P2 — Globalize the WebUI terminal fix — TODO
My earlier terminal-toolset fix was a LOCAL ~/.hermes/config.yaml edit. Need: make `terminal` a guaranteed in-repo default for the api_server platform + regression test, so a fresh clone's WebUI can run shell commands with no manual config.

### P-skills — skills_list timeline bloat (frontend) — DONE + BROWSER-PROVEN
User complaint: /app thinking blocks dumped the WHOLE skills catalog JSON (every name+description).
Fix (workspace repo, branch feat/parallel-agents-sse-cockpit, commit ec66baa): skills_list now
renders headline "Listed N skills" + names-only body (no descriptions/JSON), via
isSkillListTool/extractSkillNames/skillListStepStatus in toolLabels.ts + a branch in
CustomToolRenderer.tsx. 18 jest tests, types+lint clean. BROWSER E2E: real skills_list call in
/app → "Listed 313 skills" + comma-separated names only (screenshot skills-list-names-only-AFTER.png).
Standing rule saved to memory (feedback_webui_skill_names_not_content).

### P-verify — WebUI end-to-end smoke — DONE
Gateway /v1/{health,models,toolsets,skills,capabilities} all 200; /app{,/memory,/parallel-agents,
/agents,/open-design} all 200; skills endpoint = 313 names+desc+category only (lazy bodies);
plugin discovery 45 enabled no errors; gateway stable ~3h. Flagged: Honcho dialectic query
warning = pre-existing external-memory degradation, graceful, not a regression.
ADVERSARIAL SWARM (wf_b0b653f3-ca2) DONE: 5 personas + arbiter, 24 raw → 7 confirmed, 6 dismissed.
Dismissed (validated my work): discord/irc/langfuse/providers do NOT auto-connect/leak creds;
renderer robust vs adversarial input; union/opt-out correct; timeline trim hides nothing the model needs.
ALL 7 CONFIRMED FIXED + verified (157 py tests, 21 jest, types+lint, browser re-proven):
  - HIGH: dreaming shipped default-RUNNING (unconsented bg LLM + MEMORY.md mutation). FIX: dreaming
    config default enabled=False (plugins/dreaming/config.py) — loads dormant, runs only on
    explicit dreaming.enabled:true. User's machine unaffected (has explicit enabled:true). Verified.
  - MED: oc_docs_search `search` toolset rides default-on past explicit platform_toolsets override.
    KEPT (it is the user's "plugins everywhere" intent; changing core would strip plugin toolsets
    from the explicitly-configured cli platform) — pinned via positive test assertion instead.
  - MED: override test relaxed → strengthened to assert extras ⊆ {search,team} (a future sensitive
    default-on toolset now trips it).
  - LOW x4: stale doc path; dead `enabled is not None` guard removed; proactivity docstring clarified
    (loads-by-default but behavior-gated); skills_list headline now uses backend `count`; tool-name
    match case-insensitive.

### P3 — oc/OpenComputer command + branding rebrand — ~80% DONE
Entry points already done (oc/opencomputer primary, hermes alias). DONE this session:
- Agent help-guidance now says the CLI is `oc` → LIVE-VERIFIED the agent answers `oc plugins list` (was `hermes plugins list`). Commit 7e319b5ef.
- setproctitle/prctl/pthread_setname_np → `opencomputer`. Commit 7e319b5ef.
- 181 user-facing `hermes <subcmd>` help/error/agent strings → `oc` across 42 display files. Commit 4c47d6bda. Verified: 44 files compile, oc --help/plugins/doctor work, preserve tokens intact (~/.hermes 520 refs, HERMES_*, hermes_cli, hermes-agent pkg, hermes alias all untouched), ZERO new test failures (17 gateway/service failures are PRE-EXISTING on the branch — sources untouched by me; FLAG for separate investigation).
REMAINING (cosmetic, lower-value): banner ASCII logo + HERMES_CADUCEUS symbol → OpenComputer brand; TUI brand strings (ui-tui/). Service/setup/migration files (gateway.py etc.) deliberately LEFT on the working `hermes` alias (rewriting generated service defs is risky for no functional gain).
All pushed to PR #9 (sakshamzip2-sys/hermes-agent#9).

### Items 2 & 3 (user: "do them all one by one") — REMAINING
- Item 2: deeper WebUI command verification (chat/terminal/skills_list/endpoints/pages/lazy-load already verified; could drive more tools e.g. web_search/file-ops/delegate).
- Item 3: investigate the pre-existing `Honcho dialectic query failed` warnings (memory-enrichment path).
- Also flagged: 17 pre-existing gateway/service-manager test failures on this branch (systemd/service tests).

## Commits
- 3beefee36 feat(plugins): ship a curated default-enabled plugin loadout
- 62c4e691f test(api_server): lock terminal into the WebUI default toolset
- ec66baa (workspace) fix(timeline): render skills_list as names-only
