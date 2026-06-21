---
name: gateway-control
description: Use when the user wants to check or control the running agent gateway/service: 'is the gateway up', 'gateway status', 'restart the gateway', or 'reload MCP servers'. Restart and reload interrupt the running service and are confirmed first.
---

# Gateway control

- Status: report whether the gateway/service is reachable (read-only).
- RESTART / RELOAD (consequential): restarting the gateway or reloading MCP
  interrupts active sessions. Confirm before doing it.
