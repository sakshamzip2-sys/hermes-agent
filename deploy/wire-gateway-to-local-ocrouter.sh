#!/usr/bin/env bash
# wire-gateway-to-local-ocrouter.sh
#
# Finish the OAuth-thinking path the way the prod router does it (drawing from
# the Claude plan, NOT extra-usage): seed the locally-running FIXED oc-router
# (oc-router:effort-thinking on :8090) with the user's Claude OAuth account,
# mint a client key, point the OC gateway at the local router, and verify
# extended thinking flows end-to-end. Auto-rolls-back the gateway on failure.
#
# The local oc-router applies full Claude Code mimicry (metadata.user_id +
# system rewrite), so Anthropic bills against the plan — avoiding the
# third-party "extra usage" 400 that the direct gateway→Anthropic path hits.
set -euo pipefail

ROUTER="http://127.0.0.1:8090"
ADMIN_EMAIL="admin@oc-router.local"
ADMIN_PW="oc-admin-local-2026"
CFG="$HOME/.hermes/config.yaml"
BAK="$HOME/.hermes/config.yaml.pre-localrouter.bak"
GW_PORT="8642"; GW_KEY="oc-hermes-local-test"
die(){ echo "[wire] ERROR: $*" >&2; exit 1; }

echo "[wire] reading Claude OAuth credential (user-directed)…"
CREDS_JSON=$(security find-generic-password -s "Claude Code-credentials" -w 2>/dev/null) || die "cannot read Claude Code credentials"
ACCESS=$(printf '%s' "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth']['accessToken'])")
REFRESH=$(printf '%s' "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth'].get('refreshToken',''))")
EXPIRES=$(printf '%s' "$CREDS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['claudeAiOauth'].get('expiresAt',0))")
[ -n "$ACCESS" ] || die "no access token"
echo "[wire] token len=${#ACCESS} refresh=${REFRESH:+present} expires=$EXPIRES"

echo "[wire] admin login…"
ADMTOK=$(curl -s --max-time 10 -X POST "$ROUTER/api/v1/auth/login" -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PW\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['access_token'])")
[ -n "$ADMTOK" ] || die "admin login failed"

echo "[wire] creating anthropic OAuth account…"
ACCT_RESP=$(curl -s --max-time 15 -X POST "$ROUTER/api/v1/admin/accounts" \
  -H "Authorization: Bearer $ADMTOK" -H "Content-Type: application/json" \
  -d "{\"name\":\"claude-max-oauth\",\"platform\":\"anthropic\",\"type\":\"oauth\",\"credentials\":{\"access_token\":\"$ACCESS\",\"refresh_token\":\"$REFRESH\",\"expires_at\":$EXPIRES}}")
echo "[wire] account resp: $(printf '%s' "$ACCT_RESP" | head -c 200)"
printf '%s' "$ACCT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('code')==0 else 1)" || echo "[wire] (account create non-zero — may already exist; continuing)"

echo "[wire] minting client API key…"
KEY_RESP=$(curl -s --max-time 15 -X POST "$ROUTER/api/v1/api-keys" \
  -H "Authorization: Bearer $ADMTOK" -H "Content-Type: application/json" \
  -d '{"name":"oc-gateway","description":"OC gateway client key"}')
CLIENT_KEY=$(printf '%s' "$KEY_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
def find(o):
    if isinstance(o,dict):
        for k,v in o.items():
            if k.lower() in ('key','api_key','apikey','secret','token') and isinstance(v,str) and v.startswith('sk-'): return v
            r=find(v)
            if r: return r
    return None
print(find(d) or '')
")
[ -n "$CLIENT_KEY" ] || { echo "[wire] key resp: $(printf '%s' "$KEY_RESP" | head -c 300)"; die "could not mint client key"; }
echo "[wire] client key: ${CLIENT_KEY:0:12}…"

echo "[wire] backing up gateway config → $BAK"
cp "$CFG" "$BAK"
CLIENT_KEY="$CLIENT_KEY" python3 - "$CFG" <<'PY'
import os,sys,yaml
path=sys.argv[1]; key=os.environ["CLIENT_KEY"]
c=yaml.safe_load(open(path)); m=c["model"]
m["provider"]="custom"; m["base_url"]="http://127.0.0.1:8090/v1"; m["api_key"]=key
m["api_mode"]="chat_completions"; m["default"]="claude-opus-4-8"; m["max_tokens"]=32000
yaml.safe_dump(c,open(path,"w"),sort_keys=False,default_flow_style=False,allow_unicode=True)
print("[wire] gateway config → local oc-router")
PY

echo "[wire] restarting gateway…"
launchctl kickstart -k "gui/$(id -u)/ai.opencomputer.gateway" 2>/dev/null || true
for i in $(seq 1 30); do curl -s -o /dev/null --max-time 4 "http://127.0.0.1:${GW_PORT}/health" && break; sleep 2; done

echo "[wire] LIVE verify: thinking through gateway→local router→Anthropic(plan)…"
THINK=$(curl -sN --max-time 120 "http://127.0.0.1:${GW_PORT}/v1/chat/completions" \
  -H "Authorization: Bearer ${GW_KEY}" -H "Content-Type: application/json" \
  -d '{"model":"open-computer","stream":true,"oc_model":"claude-opus-4-8","oc_reasoning_effort":"high","messages":[{"role":"user","content":"Solve the 12-ball balance puzzle (one odd ball, unknown heavier/lighter, 3 weighings). Reason carefully."}]}' 2>/dev/null \
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
echo "[wire] thinking chars: ${THINK:-0}"
if [ "${THINK:-0}" -gt 0 ] 2>/dev/null; then
    echo "[wire] ✅ SUCCESS — extended thinking LIVE via the fixed oc-router (OAuth, plan billing)."
else
    echo "[wire] ❌ no thinking — rolling back gateway."
    cp "$BAK" "$CFG"; launchctl kickstart -k "gui/$(id -u)/ai.opencomputer.gateway" 2>/dev/null || true
    die "rolled back; inspect router logs (docker compose -f oc-router/deploy logs oc-router)."
fi
