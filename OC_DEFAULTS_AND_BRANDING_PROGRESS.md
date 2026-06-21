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

### P3 — oc/OpenComputer command + branding rebrand — TODO (designed, gated on brainstorm approval)
Entry points already done (oc/opencomputer primary, hermes alias). Remaining: banner logo + caduceus, setproctitle, TUI brand, scattered `hermes <subcmd>` help/error strings → `oc`, and a help-guidance line so the WebUI agent uses `oc`. Surgical display-string rebrand governed by CHANGE/PRESERVE denylist (never touch ~/.hermes dir, HERMES_* env, hermes_cli module names, upstream URLs).

### P4 — Verify + council review + commit — TODO

## Commits
- (pending) feat(plugins): ship a curated default-enabled plugin loadout
