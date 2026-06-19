#!/usr/bin/env bash
# switch-gateway-to-anthropic-oauth.sh
#
# Make the OC gateway send chat DIRECTLY to api.anthropic.com using a Claude
# Code OAuth *setup-token* (minted with `claude setup-token` for external use),
# instead of via router.tryopencomputer.com (which strips thinking/effort).
#
# OC's anthropic transport (agent/anthropic_adapter.py) sends the required
# Bearer + `anthropic-beta: oauth-2025-04-20` + Claude Code identity system
# prompt, and honors per-request reasoning_config → native extended thinking.
# Net: the prompt-bar effort/thinking picker becomes REAL end-to-end.
#
# Usage:
#   bash switch-gateway-to-anthropic-oauth.sh <sk-ant-oat01-...setup-token>
# (or export CLAUDE_CODE_OAUTH_TOKEN and run with no arg)
#
# Safe: backs up config.yaml, verifies thinking with a live request, and ROLLS
# BACK automatically if thinking doesn't come back.
set -euo pipefail

TOKEN="${1:-${CLAUDE_CODE_OAUTH_TOKEN:-}}"
CFG="$HOME/.hermes/config.yaml"
BAK="$HOME/.hermes/config.yaml.pre-anthropic-oauth.bak"
GW_PORT="${API_SERVER_PORT:-8642}"
GW_KEY="${API_SERVER_KEY:-oc-hermes-local-test}"

die(){ echo "[switch] ERROR: $*" >&2; exit 1; }
[ -n "$TOKEN" ] || die "no token. Run: claude setup-token  then pass it as arg 1."
[ -f "$CFG" ] || die "config not found: $CFG"
case "$TOKEN" in sk-ant-oat*|sk-ant-api*|cc-*) ;; *) die "token must be sk-ant-oat*/sk-ant-api*/cc-*";; esac

echo "[switch] backing up config → $BAK"
cp "$CFG" "$BAK"

echo "[switch] rewriting model provider → anthropic (api.anthropic.com, anthropic_messages)"
TOKEN="$TOKEN" python3 - "$CFG" <<'PY'
import os, sys, re
try:
    import yaml
except Exception:
    yaml = None
path = sys.argv[1]
tok = os.environ["TOKEN"]
with open(path) as f:
    text = f.read()

if yaml is not None:
    cfg = yaml.safe_load(text) or {}
    m = cfg.setdefault("model", {})
    m["provider"] = "anthropic"
    m["base_url"] = "https://api.anthropic.com"
    m["api_key"] = tok
    m["api_mode"] = "anthropic_messages"
    # Default to a current real Anthropic id (the picker overrides per-request).
    if str(m.get("default","")).strip() in ("", "claude-opus-4-6"):
        m["default"] = "claude-opus-4-8"
    prov = cfg.setdefault("providers", {}).setdefault("anthropic", {})
    prov["api_key"] = tok
    prov["base_url"] = "https://api.anthropic.com"
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    print("[switch] config.yaml rewritten via yaml")
else:
    sys.exit("PyYAML unavailable; cannot safely edit config")
PY

echo "[switch] restarting gateway daemon"
if launchctl list 2>/dev/null | grep -q ai.opencomputer.gateway; then
    launchctl kickstart -k "gui/$(id -u)/ai.opencomputer.gateway"
else
    echo "[switch] (no launchd gateway; restart your gateway manually)"
fi

echo "[switch] waiting for gateway health…"
for i in $(seq 1 30); do
    if curl -s -o /dev/null --max-time 4 "http://127.0.0.1:${GW_PORT}/health"; then break; fi
    sleep 2
done

echo "[switch] LIVE verify: streaming a reasoning prompt through the gateway, counting thinking deltas…"
THINK=$(curl -sN --max-time 90 "http://127.0.0.1:${GW_PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${GW_KEY}" -H "Content-Type: application/json" \
  -d '{"model":"open-computer","stream":true,"oc_model":"claude-opus-4-8","oc_reasoning_effort":"high","messages":[{"role":"user","content":"What is 17*23? Think step by step and show your reasoning."}]}' 2>/dev/null \
  | python3 -c "
import sys,json
rc=0
for line in sys.stdin:
    line=line.strip()
    if not line.startswith('data:'): continue
    p=line[5:].strip()
    if p=='[DONE]': break
    try: d=json.loads(p)
    except: continue
    de=(d.get('choices') or [{}])[0].get('delta',{}) or {}
    r=de.get('reasoning_content') or de.get('reasoning')
    if r: rc+=len(str(r))
print(rc)
")
echo "[switch] thinking chars returned: ${THINK:-0}"

if [ "${THINK:-0}" -gt 0 ] 2>/dev/null; then
    echo "[switch] ✅ SUCCESS — extended thinking now flows through the gateway. Effort/thinking picker is LIVE."
    echo "[switch] (rollback backup kept at $BAK)"
else
    echo "[switch] ❌ no thinking returned — rolling back to preserve a working gateway."
    cp "$BAK" "$CFG"
    launchctl kickstart -k "gui/$(id -u)/ai.opencomputer.gateway" 2>/dev/null || true
    die "rolled back. Check the token (claude setup-token) and gateway logs."
fi
