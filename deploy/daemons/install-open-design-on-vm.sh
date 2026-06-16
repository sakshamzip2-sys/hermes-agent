#!/usr/bin/env bash
# install-open-design-on-vm.sh — install + run the in-OpenComputer "Open Design"
# panel backend (daemon :17456 + web :17573) as a 24/7 systemd service on a
# provisioned VM. Idempotent. Run AS ROOT on the VM, e.g. via Tailscale SSH:
#
#   tailscale ssh root@agent-<id> 'bash -s' < deploy/daemons/install-open-design-on-vm.sh
#
# Why a script (not just cloud-init): lets you retrofit an EXISTING VM now. The
# same steps are mirrored into provisioner.ts so NEW VMs get it at boot.
set -euo pipefail

OD_REPO_URL="${OD_REPO_URL:-https://github.com/nexu-io/open-design}"
OD_DIR="${OD_DIR:-/opt/open-design}"
NODE_VER="${NODE_VER:-24.16.0}"
NODE_DIR="/opt/node${NODE_VER%%.*}"          # /opt/node24
DAEMON_PORT="${DAEMON_PORT:-17456}"
WEB_PORT="${WEB_PORT:-17573}"

log(){ printf '\n[od-install] %s\n' "$*"; }

# 1. Node 24 side-by-side (do NOT disturb the system Node 22 the agent-browser uses)
if [ ! -x "$NODE_DIR/bin/node" ]; then
  log "installing Node ${NODE_VER} into ${NODE_DIR}"
  arch="$(uname -m)"; case "$arch" in x86_64) na=x64;; aarch64|arm64) na=arm64;; *) echo "unsupported arch $arch"; exit 1;; esac
  tmp="$(mktemp -d)"
  curl -fsSL "https://nodejs.org/dist/v${NODE_VER}/node-v${NODE_VER}-linux-${na}.tar.xz" -o "$tmp/node.tar.xz"
  mkdir -p "$NODE_DIR"
  tar -xJf "$tmp/node.tar.xz" -C "$NODE_DIR" --strip-components=1
  rm -rf "$tmp"
fi
export PATH="$NODE_DIR/bin:$PATH"
log "node $(node --version)"
corepack enable 2>/dev/null || true

# 2. Clone (or update) Open Design
if [ ! -d "$OD_DIR/.git" ]; then
  log "cloning $OD_REPO_URL -> $OD_DIR"
  git clone --depth 1 "$OD_REPO_URL" "$OD_DIR"
else
  log "updating existing clone"; git -C "$OD_DIR" pull --ff-only || true
fi

# 3. Install deps + build the web app (prod build; dev compile is too slow at boot)
cd "$OD_DIR"
log "pnpm install"; corepack pnpm install --frozen-lockfile
log "building @open-design/web"; corepack pnpm --filter @open-design/web build

# 4. systemd unit — keep daemon+web alive 24/7, loopback only (no public listener)
log "writing /etc/systemd/system/oc-open-design.service"
cat > /etc/systemd/system/oc-open-design.service <<EOF
[Unit]
Description=OpenComputer — Open Design panel backend (daemon + web)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${OD_DIR}
Environment=PATH=${NODE_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=OD_DATA_DIR=/root/.od
# 'run' (not 'start') stays in the foreground so systemd supervises it; this
# launches daemon (${DAEMON_PORT}) + web (${WEB_PORT}), no Electron desktop.
ExecStart=${NODE_DIR}/bin/corepack pnpm exec tools-dev run --daemon-port ${DAEMON_PORT} --web-port ${WEB_PORT} --prod
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=oc-open-design

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now oc-open-design.service

# 5. Verify
log "waiting for daemon health…"
ok=0
for i in $(seq 1 30); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${DAEMON_PORT}/api/health" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
if [ "$ok" = 1 ]; then
  log "OK — Open Design daemon healthy: $(curl -fsS http://127.0.0.1:${DAEMON_PORT}/api/health)"
else
  log "WARN — daemon not healthy yet; check: journalctl -u oc-open-design -n 80 --no-pager"
  exit 1
fi
log "done."
