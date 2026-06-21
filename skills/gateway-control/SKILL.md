---
name: gateway-control
destructive: true
description: Use when the user wants to check or control the running agent gateway/service: 'is the gateway up', 'gateway status', 'restart the gateway', or 'reload MCP servers'. Restart and reload interrupt the running service and are confirmed first.
---

# Gateway control

Control the messaging gateway / background service via `hermes gateway` (terminal).

## Read-only (run autonomously)
- Status: `hermes gateway status` (is the gateway reachable).
- List all profiles' gateway status: `hermes gateway list`.

## Run / lifecycle (consequential, confirm first)
- Foreground run: `hermes gateway run` (recommended for WSL/debug).
- Start the installed background service: `hermes gateway start`.
- STOP the service: `hermes gateway stop` (interrupts active sessions).
- RESTART the service: `hermes gateway restart` (interrupts active sessions).
- Reload MCP servers: interrupts in-flight MCP calls.

## Install / configure (consequential, confirm first)
- Install as a systemd/launchd background service: `hermes gateway install`.
- UNINSTALL the service: `hermes gateway uninstall` (removes the background daemon).
- Configure messaging platforms: `hermes gateway setup`.
- Enroll with a relay connector: `hermes gateway enroll` (writes credentials).

Restart, stop, reload, install, uninstall, and enroll change the running service or
its credentials. State exactly what will happen and confirm before running.
