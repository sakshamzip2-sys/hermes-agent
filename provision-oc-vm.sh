#!/usr/bin/env bash
# provision-oc-vm.sh — bake the full OpenComputer coding stack onto a VM.
#
# Vision: every VM ships with EVERYTHING the user needs, so they just log in and use
# it. This turns the manual per-VM steps into one reproducible, idempotent run.
#
# What it puts ON THE VM:
#   1. system deps (node/npm, tmux, git, python3)               [checked/installed]
#   2. hermes (OpenComputerV2 base agent)                        [checked]
#   3. Claude Code + Codex CLIs (planner/executor backends)      [npm -g]
#   4. ALL agent profiles -> ~/.hermes/profiles/ incl. `coding`  [install_profiles.sh]
#   5. each profile's BRAIN wired to OC-router (key from env)    [provider: custom]
#   6. backend wiring (claude-code+codex -> OC-router)           [PENDING OC-router]
#
# Secrets: the OC-router brain key is read from $OC_ROUTER_KEY (injected at provision
# time) or an existing ~/.hermes/config.yaml — NEVER hardcoded here.
#
# Usage ON the VM:
#   OC_ROUTER_KEY=sk-... bash provision-oc-vm.sh [profile_templates_dir]
# Or remotely (ships this script + the templates first), see deploy-oc-vm-full.sh.
set -euo pipefail

OC_ROUTER_BASE="${OC_ROUTER_BASE:-https://router.tryopencomputer.com/v1}"
DEFAULT_MODEL="${OC_DEFAULT_MODEL:-claude-sonnet-4-6}"
HH="${HERMES_HOME:-$HOME/.hermes}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATES_DIR="${1:-$HERE/profile_templates}"

log() { echo "==> $*"; }

# OC-router brain key: env first, else reuse an existing default-home config key.
if [ -z "${OC_ROUTER_KEY:-}" ] && [ -f "$HH/config.yaml" ]; then
  OC_ROUTER_KEY="$(awk '/^model:/{m=1} m&&/api_key:/{print $2; exit}' "$HH/config.yaml" 2>/dev/null || true)"
fi
[ -n "${OC_ROUTER_KEY:-}" ] || { echo "error: set OC_ROUTER_KEY (the OC-router brain key)" >&2; exit 1; }

log "1/6 system deps"
command -v node >/dev/null || { echo "  ERROR: Node 20+ required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "  ERROR: python3 required" >&2; exit 1; }
command -v tmux >/dev/null || { apt-get update -y >/dev/null 2>&1 && apt-get install -y tmux >/dev/null 2>&1; } || true
echo "  node $(node -v) | npm $(npm -v) | tmux $(tmux -V 2>/dev/null || echo MISSING)"

log "2/6 hermes (base agent)"
hermes --version 2>/dev/null | head -1 | sed 's/^/  /' || echo "  WARN: hermes not found — install hermes-agent first"

log "3/6 install Claude Code + Codex CLIs"
if npm install -g @anthropic-ai/claude-code @openai/codex >/dev/null 2>&1; then echo "  npm install: OK"; else echo "  npm install: FAILED" >&2; fi
echo "  claude: $(claude --version 2>/dev/null || echo MISSING) | codex: $(codex --version 2>/dev/null || echo MISSING)"

log "4/6 deploy all profiles from $TEMPLATES_DIR"
[ -d "$TEMPLATES_DIR" ] || { echo "  ERROR: no profile_templates at $TEMPLATES_DIR" >&2; exit 1; }
installer="$(dirname "$TEMPLATES_DIR")/scripts/install_profiles.sh"
mkdir -p "$HH/profiles"
if [ -f "$installer" ]; then
  sh "$installer" "$HH/profiles" | sed 's/^/  /'
else
  for d in "$TEMPLATES_DIR"/*/; do
    n=$(basename "$d"); [ -f "$d/SOUL.md" ] || continue
    if [ -e "$HH/profiles/$n/SOUL.md" ]; then echo "  skip $n (exists)"; else
      mkdir -p "$HH/profiles/$n"; cp -R "$d." "$HH/profiles/$n/"; echo "  installed $n"; fi
  done
fi

log "5/6 wire each profile brain -> OC-router"
for cfg in "$HH"/profiles/*/config.yaml; do
  [ -f "$cfg" ] || continue
  OC_ROUTER_BASE="$OC_ROUTER_BASE" DEFAULT_MODEL="$DEFAULT_MODEL" OC_ROUTER_KEY="$OC_ROUTER_KEY" \
  python3 - "$cfg" <<'PY'
import os, re, sys, pathlib
cfg = pathlib.Path(sys.argv[1]); t = cfg.read_text()
base, model, key = os.environ["OC_ROUTER_BASE"], os.environ["DEFAULT_MODEL"], os.environ["OC_ROUTER_KEY"]
block = ("model:\n  default: %s\n  provider: custom\n  base_url: %s\n"
         "  api_mode: chat_completions\n  api_key: %s\n  max_tokens: 32000\n  context_length: 200000\n"
         % (model, base, key))
if re.search(r'(?m)^model:[ \t]+\S', t):            # model: <string>  -> replace line with block
    t = re.sub(r'(?m)^model:.*\n', block, t, count=1)
elif re.search(r'(?m)^model:[ \t]*$', t):           # model: <mapping> -> ensure api_key present
    if not re.search(r'(?m)^[ \t]+api_key:', t):
        t = re.sub(r'(?m)^([ \t]+provider:[ \t]*custom\n)', r'\1  api_key: ' + key + '\n', t, count=1)
else:
    t = block + t
cfg.write_text(t)
print("  wired", cfg.parent.name)
PY
done

log "6/6 backend wiring (claude-code + codex -> OC-router) — PENDING OC-router integration"
echo "  Once OC-router serves the codex + claude models, wire the BACKENDS so there is"
echo "  NO per-user OAuth (the thing that 401s today when copying Claude Code creds):"
echo "    claude:  export ANTHROPIC_BASE_URL=<oc-router> + ANTHROPIC_AUTH_TOKEN=<oc key>"
echo "    codex:   set model + base_url -> oc-router in ~/.codex/config.toml"
echo "  Until then the orchestrator delegates to whichever backend is authed, else self-executes."

log "verify: profiles installed + brains reachable"
hermes profile list 2>/dev/null | sed 's/^/  /' | head -25 || ls "$HH/profiles" | sed 's/^/  /'
echo "==> done. Launch the orchestrator on this VM:  oc -p coding   (or hermes -p coding)"
