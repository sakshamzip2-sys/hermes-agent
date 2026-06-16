# Always-on daemons (day-one process)

Make the OpenComputer agent — and optionally the in-app Open Design panel backend —
run **24/7** as a boot-time daemon on the current machine.

```bash
# agent gateway only (OpenAI-compat API on :8642)
deploy/daemons/install-daemons.sh

# also keep the in-OpenComputer "Open Design" panel backend alive (:17456 + :17573)
deploy/daemons/install-daemons.sh --with-open-design

# remove
deploy/daemons/install-daemons.sh --uninstall
```

- **macOS** → `launchd` LaunchAgents in `~/Library/LaunchAgents` (`RunAtLoad` + `KeepAlive`).
- **Linux** → `systemd --user` units in `~/.config/systemd/user` with lingering enabled
  (`Restart=always`), so the agent runs even when you are logged out.

Token/host/port come from `API_SERVER_KEY` / `API_SERVER_HOST` / `API_SERVER_PORT`
(defaults: `oc-local-token` / `127.0.0.1` / `8642`). The frontend's
`OC_LOCAL_AGENT_TOKEN` must equal `API_SERVER_KEY`.

> On platform-provisioned VMs you do **not** need this: the agent already
> auto-starts at boot via cloud-init — see
> `oc-platform/packages/service-api/src/services/provisioner.ts`
> (`oc-gateway.service` / `oc-workspace.service`, `Restart=always`,
> `systemctl enable --now`). This script is the equivalent for a laptop or a
> self-hosted box that just cloned the repo.

The Open Design daemon here runs **only** the daemon + web that the Open Design
*side panel inside OpenComputer* talks to — it does not launch the standalone
Electron desktop app.
