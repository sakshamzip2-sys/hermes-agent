# Web UI → VM wiring (task c)

Goal: the browser (web UI) drives the **VM's** agent + desktop — the "everything on
the VM" architecture, where the user just logs in and uses the VM.

## Current state (diagnosed 2026-06-23)

| Piece | State |
|-------|-------|
| VM agent profiles (all 9, brains on OC-router) | ✅ deployed + verified (PONG) |
| VM API server `:8642` | serving, bound `127.0.0.1`, returns 401 without the API key |
| VM gateway `:8643` (0.0.0.0) | serving (404 at `/`) but NOT reachable from the Mac |
| VM host firewall (ufw) | inactive — not the blocker |
| Why `:8643` is unreachable | Tailscale ACL / Hetzner network policy for tagged-devices (tailnet-admin config), not a VM-local firewall |
| VM desktop (noVNC `:6080` → `cloudflared` → `agent-<id>-vnc.tryopencomputer.com`) | tunnel reachable (HTTP 200) |
| Web UI desktop gate | `VncViewer.tsx:109` hard-requires a **Supabase JWT**; local bypass has none → "Desktop unavailable (sign in required)" |

## Path A — PRODUCTION (the target). Gated on OC-router + Supabase.

```
browser ──(real Supabase login, JWT)──> Next BFF ──> service-api(:3001)
        ──> resolves the user's VM instance (Supabase DB) ──> Cloudflare tunnel
        ──> VM agent (chat) + VM desktop (noVNC)
```

Steps to turn it on (when the pending pieces are ready):
1. **OC-router backends** (the #1 you're waiting on): wire claude-code + codex on the
   VM to OC-router so the VM agent truly delegates with NO per-user OAuth. (Until then
   the VM orchestrator self-falls-back — proven.)
2. **Real login**: stop the local bypass so the web UI uses Supabase auth. In
   `workspace/.env.local`, comment out `OC_LOCAL_AGENT_URL` (and `OC_LOCAL_AGENT_TOKEN`),
   restart the frontend. Now `/auth/login` is live and a session yields the JWT the
   Computer panel needs.
3. **VM registered as the user's instance** in Supabase so service-api resolves it, and
   its Cloudflare tunnel (`agent-<id>-*.tryopencomputer.com`) is live. The deployed VM's
   VNC tunnel already returns 200, so the tunnel mechanism works.
4. Result: log in → the browser drives the VM agent + sees the VM desktop. No local
   anything.

Trade-off to remember: `OC_LOCAL_AGENT_URL` does double duty (bypass login AND route to
the local `:8642`). Turning it off for real login also turns off local-agent routing —
which is correct here, because the target is the VM, not the local Mac.

## Path B — DEV shortcut (optional, fragile). Connect web UI → VM over Tailscale now.

Only if you want a connection before Path A's pieces land. Caveats: bypasses the
product auth path, and the VM backends are still 401 until OC-router (so the VM
orchestrator self-executes rather than delegating).

1. Expose the VM API server over the tailnet (the port ACL blocks direct `:8642`):
   `ssh root@<vm> 'tailscale serve --bg --https=8642 http://127.0.0.1:8642'`
   (or open the tailnet ACL for `:8643`).
2. In `workspace/.env.local`: `OC_LOCAL_AGENT_URL=https://<vm-magicdns-name>:8642` and
   `OC_LOCAL_AGENT_TOKEN=<the VM's API_SERVER_KEY>`; restart the frontend.
3. The browser now drives the VM agent (chat). Desktop still needs Path A (Supabase JWT).

## Bottom line

The full web-UI→VM wiring is gated on the **same OC-router/Supabase pieces** you chose
to wait on — Path A is the product target and is ready to switch on per the steps above
once those land. The VM itself is fully provisioned (all agents + brains on OC-router).
