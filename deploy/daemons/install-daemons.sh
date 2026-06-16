#!/usr/bin/env bash
# install-daemons.sh — make the OpenComputer agent (and optionally the in-app
# Open Design panel backend) run 24/7 as a boot-time daemon on THIS machine.
#
#   macOS  -> launchd LaunchAgents (~/Library/LaunchAgents)
#   Linux  -> systemd --user units (~/.config/systemd/user), lingering enabled
#
# This is the "day-one process" wiring for anyone who clones the repo. On
# platform-provisioned Hetzner VMs the agent already auto-starts via cloud-init
# (oc-platform .../provisioner.ts -> oc-gateway.service, Restart=always); this
# script is the equivalent for a developer's own machine / self-hosted box.
#
# Usage:
#   deploy/daemons/install-daemons.sh                 # agent gateway only
#   deploy/daemons/install-daemons.sh --with-open-design
#   deploy/daemons/install-daemons.sh --uninstall
#
# Env overrides:
#   API_SERVER_KEY   (default: oc-local-token)   token the frontend must match
#   API_SERVER_HOST  (default: 127.0.0.1)
#   API_SERVER_PORT  (default: 8642)
#   OD_REPO          (default: ../../../open-design relative to this repo)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"   # the agent repo root
OD_REPO="${OD_REPO:-$(cd "$REPO_DIR/.." && pwd)/open-design}"
API_SERVER_KEY="${API_SERVER_KEY:-oc-local-token}"
API_SERVER_HOST="${API_SERVER_HOST:-127.0.0.1}"
API_SERVER_PORT="${API_SERVER_PORT:-8642}"
WITH_OD=0; UNINSTALL=0
for a in "$@"; do
  case "$a" in
    --with-open-design) WITH_OD=1 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

OC_BIN="$REPO_DIR/.venv/bin/oc"
[ -x "$OC_BIN" ] || OC_BIN="$REPO_DIR/.venv/bin/hermes"   # editable-install fallback
PY_BIN="$REPO_DIR/.venv/bin/python3"

log(){ printf '  %s\n' "$*"; }

# --------------------------------------------------------------------------
install_macos(){
  local LA="$HOME/Library/LaunchAgents" LOGS="$HOME/Library/Logs/opencomputer"
  mkdir -p "$LA" "$LOGS"
  local plist="$LA/ai.opencomputer.gateway.plist"
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.opencomputer.gateway</string>
  <key>ProgramArguments</key><array>
    <string>$PY_BIN</string><string>$OC_BIN</string><string>gateway</string><string>run</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>API_SERVER_KEY</key><string>$API_SERVER_KEY</string>
    <key>API_SERVER_HOST</key><string>$API_SERVER_HOST</string>
    <key>API_SERVER_PORT</key><string>$API_SERVER_PORT</string>
    <key>PATH</key><string>$REPO_DIR/.venv/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOGS/gateway.out.log</string>
  <key>StandardErrorPath</key><string>$LOGS/gateway.err.log</string>
</dict></plist>
EOF
  launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null || launchctl enable "gui/$(id -u)/ai.opencomputer.gateway" || true
  launchctl kickstart -k "gui/$(id -u)/ai.opencomputer.gateway" 2>/dev/null || true
  log "agent gateway -> $plist"

  if [ "$WITH_OD" = 1 ]; then
    [ -d "$OD_REPO" ] || { echo "open-design not found at $OD_REPO (set OD_REPO)"; return; }
    local node24; node24="$(ls -d "$HOME"/.nvm/versions/node/v24* 2>/dev/null | tail -1)"
    local wrap="$HOME/.local/bin/oc-opendesign-daemon.sh"; mkdir -p "$(dirname "$wrap")"
    cat > "$wrap" <<EOF
#!/bin/bash
set -u
export PATH="${node24:+$node24/bin:}\$PATH"
cd "$OD_REPO" || exit 1
corepack pnpm exec tools-dev start daemon --daemon-port 17456 --web-port 17573 || true
corepack pnpm exec tools-dev start web    --daemon-port 17456 --web-port 17573 || true
while curl -fsS --max-time 4 http://127.0.0.1:17456/api/health >/dev/null 2>&1 \
   && curl -fsS --max-time 4 -o /dev/null http://127.0.0.1:17573 2>/dev/null; do sleep 20; done
exit 1
EOF
    chmod +x "$wrap"
    local odplist="$LA/ai.opencomputer.opendesign.plist"
    cat > "$odplist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.opencomputer.opendesign</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$wrap</string></array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/><key>ThrottleInterval</key><integer>20</integer>
  <key>StandardOutPath</key><string>$LOGS/opendesign.out.log</string>
  <key>StandardErrorPath</key><string>$LOGS/opendesign.err.log</string>
</dict></plist>
EOF
    launchctl bootstrap "gui/$(id -u)" "$odplist" 2>/dev/null || true
    launchctl kickstart "gui/$(id -u)/ai.opencomputer.opendesign" 2>/dev/null || true
    log "open-design panel -> $odplist"
  fi
}

uninstall_macos(){
  for j in ai.opencomputer.gateway ai.opencomputer.opendesign; do
    launchctl bootout "gui/$(id -u)/$j" 2>/dev/null || true
    rm -f "$HOME/Library/LaunchAgents/$j.plist"
  done
  log "removed launchd agents"
}

# --------------------------------------------------------------------------
install_linux(){
  local UD="$HOME/.config/systemd/user"; mkdir -p "$UD"
  cat > "$UD/oc-gateway.service" <<EOF
[Unit]
Description=OpenComputer Agent Gateway
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
Environment=API_SERVER_KEY=$API_SERVER_KEY
Environment=API_SERVER_HOST=$API_SERVER_HOST
Environment=API_SERVER_PORT=$API_SERVER_PORT
WorkingDirectory=$REPO_DIR
ExecStart=$PY_BIN $OC_BIN gateway run
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
EOF
  loginctl enable-linger "$(id -un)" 2>/dev/null || true   # run even when logged out
  systemctl --user daemon-reload
  systemctl --user enable --now oc-gateway.service
  log "agent gateway -> $UD/oc-gateway.service (systemctl --user status oc-gateway)"
  [ "$WITH_OD" = 1 ] && log "NOTE: --with-open-design on Linux: ensure Node 24 + corepack pnpm, then mirror the unit for: (cd open-design && corepack pnpm exec tools-dev start daemon|web --daemon-port 17456 --web-port 17573)"
}

uninstall_linux(){
  systemctl --user disable --now oc-gateway.service 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/oc-gateway.service"; systemctl --user daemon-reload || true
  log "removed systemd --user unit"
}

# --------------------------------------------------------------------------
case "$(uname -s)" in
  Darwin) [ "$UNINSTALL" = 1 ] && uninstall_macos || install_macos ;;
  Linux)  [ "$UNINSTALL" = 1 ] && uninstall_linux || install_linux ;;
  *) echo "unsupported OS: $(uname -s)"; exit 1 ;;
esac
echo "done."
