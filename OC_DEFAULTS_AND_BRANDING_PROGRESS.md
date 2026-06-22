# OC Defaults + Branding Mission — RESUME / PROGRESS

> **RESUME CHEAPLY:** read THIS file + `git log --oneline origin/main..HEAD`, NOT the chat transcript.
> Branch `feat/oc-default-plugins-and-branding` on `origin` (sakshamzip2-sys/hermes-agent fork).
> Open draft PRs: **OpenComputerV2 → sakshamzip2-sys/hermes-agent#9**, **workspace → Open-Computer-AI/workspace#22**.
> Verify state: `cd OpenComputerV2 && git log --oneline origin/main..HEAD` (9 commits) + `git status` (should be clean).

## Mission
Ship the owner's curated setup as repo defaults (any clone inherits it) + make `oc`/`opencomputer`
the primary command & brand (hermes stays a silent back-compat alias). No plumbing rename
(upstream-merge-safe). GATE: no PR merge to a protected branch without explicit user go.

## DONE + committed + pushed (PR #9 / #22), all verified
1. **Default-enabled plugin loadout** (3beefee36) — DEFAULT_ENABLED_PLUGINS (24 keys: model-providers,
   image/video gen, langfuse, security-guidance, discord/irc, oc_* parallel-agents, dreaming/etc.)
   unioned into loader + `oc plugins list`. Fresh empty HERMES_HOME shows them enabled, no config written.
2. **Terminal locked into WebUI default** (62c4e691f) — regression test at the _get_platform_tools layer.
3. **dreaming made default-OFF** (73279f902) — config enabled=False so it LOADS but only RUNS on
   explicit `dreaming.enabled:true` (caught by 1st adversarial swarm: HIGH consent issue). User's
   ~/.hermes has dreaming.enabled:true so their machine unaffected.
4. **Agent uses `oc` + proctitle** (7e319b5ef) — HERMES_AGENT_HELP_GUIDANCE says CLI is `oc`;
   setproctitle->opencomputer. LIVE-VERIFIED: agent answers `oc plugins list`.
5. **181 command-string sweep** (4c47d6bda) — `hermes <subcmd>` -> `oc <subcmd>` across 42 display files.
6. **Symmetric caduceus banner** (8a1200a9a) — kept the caduceus + pink + OPENCOMPUTER wordmark
   (user explicitly: keep banner, keep pink, NOT yellow/hermes); only fixed L/R symmetry via braille mirror.
7. **TUI brand strings** (1605ea037) + **verification-swarm fixes** (8c4e4f50e): reverted a `~/.oc`
   over-reach in skills_sync.py, rebranded missed `auth` strings in status.py + synced 3 test_status.py
   asserts (the one green->red test the sweep introduced is now fixed), tools_config.py (10), tips.py
   (1 line), 2 TUI strings.
   - Skills-list names-only frontend fix lives in workspace PR #22 (ec66baa, 3849269).

## Verification done (real evidence)
- Fresh-clone plugin proof; 157 py tests + 21 jest green for the shipped features; types+lint clean.
- 2 browser proofs (skills_list "Listed 313 skills" names-only; agent runs `oc`/terminal).
- TWO unbiased adversarial swarms run; ALL confirmed findings fixed.

## STATUS 2026-06-22 (post account-switch): Item A COMPLETE. Item B gated.
- **Item A (rebrand) — DONE + VERIFIED.** Finished the missed tokens (auth/sessions/portal/billing/flags):
  commit 5e05add13 (79 strings/40 files via fixed lookbehind regex + 8 test-assertion syncs) and
  f3e39fcb0 (profile wrapper scripts -> `oc -p`; resolved 9 pre-existing wrapper tests, zero regressions).
  FINAL PROOF: 231 branding/plugin/cli tests pass; oc/opencomputer/hermes all resolve; completeness
  grep = 0 user-facing `hermes <cmd>` display strings left. HEAD f3e39fcb0, pushed to PR #9.
- **Item B (Honcho 402) — GATED, fix fully prepared.** Root cause = OpenRouter 402 (out of credits;
  8192-token request, 847 affordable). Knob found: `DIALECTIC_MAX_OUTPUT_TOKENS` in honcho/.env
  (unset -> defaults 8192). Proven `max_tokens=800` clears the 402. The reversible stopgap (append
  `DIALECTIC_MAX_OUTPUT_TOKENS=800` + recreate honcho-api) was BLOCKED by the auto-mode classifier as
  a gated shared-infra change — correct: needs explicit user go on WHICH fix (add OR credits / apply
  the 800 stopgap / migrate provider). honcho/.env is untouched/clean. See [[project_honcho_dialectic_402]].

## (historical) earlier-planned next steps — Item A now done; kept for reference

### A. Finish the command-string rebrand (sweep was INCOMPLETE)
The original sweep's subcmd alternation MISSED tokens: `auth`, `sessions`, `portal`, `billing`, and
flags (`-c`,`-w`,`-q`,`-Q`). So user-facing `hermes <those>` strings remain in: agent/auxiliary_client.py,
agent/conversation_loop.py, cli.py, hermes_cli/_parser.py, hermes_cli/auth.py, hermes_cli/cli_agent_setup_mixin.py,
hermes_cli/cli_commands_mixin.py, cron/__init__.py (+ check doctor/profiles/skills_hub).
**Find them (git-grep ERE, NOT python-regex):**
```
git grep -nE "hermes (auth|sessions|portal|billing|-[a-zA-Z]|chat|gateway|tools|model|plugins|skills|doctor|setup|status|profile|cron|mcp|workspace|update|enroll)" -- '*.py' '*.ts' '*.tsx' \
 | grep -vE "tests/|hermes-agent|hermes_cli\.|HERMES_|import hermes|from hermes|\.hermes|nousresearch|@hermes/|hermes\.exe|launchHermes|complete -c hermes"
```
**Apply with this FIXED python regex (negative lookbehind prevents the `.hermes` over-reach):**
`re.compile(r"(?<![./~\w@-])hermes (?=(?:auth|sessions|portal|billing|chat|gateway|tools|model|cron|skills|doctor|plugins|mcp|workspace|profile|dashboard|config|memory|agents|teams|flow|runs?|status|setup|new|version|update|enroll|install|uninstall|-[a-zA-Z])\b)")` -> `"oc "`.
**PRESERVE / DENYLIST (do NOT change):** ~/.hermes, HERMES_*, hermes_cli/import hermes, hermes-agent pkg,
@hermes/ink, upstream URLs, `complete -c hermes` (completion.py shell directive), service/setup/entry files
(gateway.py, gateway_windows, service_manager, setup.py, config.py, main.py, *_windows, platform adapters,
dashboard_register, web_server, webhook, container_boot, backup, claw, mcp_config, codex_runtime_plugin_migration).
Code COMMENTS mentioning hermes are optional (low priority). After sweeping: run touched-area tests +
`grep tests/ for any 'hermes <cmd>' assertion of a string you changed` and sync those asserts.

### B. Honcho dialectic 402 (gated — money/infra; see memory project_honcho_dialectic_402)
Honcho UP; dialectic LLM = openai/gpt-4o-mini via OpenRouter returns 402 (out of credits: requests
8192 tokens, can afford 847). Degrades gracefully. FIX OPTIONS (need user pick): add OR credits /
lower Honcho max_tokens ~800 (reversible container cfg + restart) / migrate provider (NOT OC-router —
needs gpt+embeddings).

### C. Globalize the WebUI terminal fix as repo default — DONE (62c4e691f test). The local ~/.hermes
config edit is belt+suspenders; the in-repo default already includes terminal for api_server.

## Gotchas / lessons
- After any ~/.hermes/config.yaml edit, `launchctl kickstart -k gui/$(id -u)/ai.opencomputer.gateway`.
- `gh pr create` defaults base to UPSTREAM (NousResearch) — must pass `--repo sakshamzip2-sys/hermes-agent`.
- 17 pre-existing gateway/service-manager systemd test failures on this branch (fail at base too; NOT ours).
- A workflow review-agent left stray edits in api_server.py/test_api_server.py (gallery-agent artifact db
  param) — REVERTED as unattributed/out-of-mission. If wanted, redo deliberately.
- Bisect with the actual SOURCE files, not failure counts (stash/checkout churn pollutes counts).

## Commits (origin/main..HEAD)
3beefee36 plugins defaults · 62c4e691f terminal test · 73279f902 dreaming-off+review fixes ·
7e319b5ef agent-oc+proctitle · 4c47d6bda 181-string sweep · 2eda76cfa progress · 8a1200a9a banner symmetry ·
1605ea037 TUI strings · 8c4e4f50e verification-swarm fixes
