---
sidebar_position: 8
title: "Channels & Monitors"
description: "Push out-of-band events into a running agent, and let plugins watch things in the background"
---

# Channels & Monitors

Two related, model-agnostic ways to make a running OpenComputer agent react to
things happening **outside** the conversation. Both are gateway features — they
inject events into the agent's loop so it picks them up on the next turn.

## Channels — push an event into a live session

A **channel** lets any in-process source (a plugin, an MCP server the agent
spawned, a webhook bridge, a monitor) push a one-off event into the running
conversation. The agent receives it wrapped as a `<channel>` block:

```text
<channel source="ci" severity="high">
build failed on main: https://ci.example.com/run/1234
</channel>
```

From Python (e.g. inside a plugin or an in-process bridge):

```python
from tools.channels import inject_channel_event

inject_channel_event(
    "ci",                                  # source label
    "build failed on main",                # body (becomes the tag content)
    meta={"severity": "high", "run_id": "1234"},  # each key -> a tag attribute
)
```

By default the event is delivered to your gateway's configured **home channel**
(the chat where you ran `/sethome`). To target a specific conversation instead:

```python
inject_channel_event("ci", "deploy done", platform="telegram", chat_id="12345")
```

Events queue and are delivered in order on the next turn (several arriving while
the agent is busy are delivered together).

:::warning Channels are untrusted input
Channel content is injected into the conversation, so an ungated channel is a
prompt-injection vector. A bridge that forwards messages from an external sender
**must** authenticate the sender before calling `inject_channel_event`. Meta keys
are restricted to identifier-safe names and values are escaped so a crafted event
can't break out of the `<channel>` tag.
:::

## Monitors — background watchers declared by a plugin

A **monitor** is a background command a plugin declares; its output lines that
match a pattern are streamed back to the agent (tail a log, poll CI or a PR,
watch a directory). Declare them in your plugin's `plugin.yaml`:

```yaml
name: my-plugin
monitors:
  - name: ci-poller
    command: "while true; do curl -s https://ci.example.com/status; sleep 30; done"
    watch_patterns: ["FAILED", "PASSED"]   # only matching lines reach the agent
    when: always                            # or on-skill-invoke:<skill-name>
```

When the gateway starts, every `when: always` monitor on an enabled plugin is
launched automatically. Matching output lines are rate-limited and delivered to
the agent the same way background-process watch events are. A monitor with
`when: on-skill-invoke:deploy` starts the first time the `deploy` skill runs.

Monitors are ordinary background processes, so you manage them with the existing
process commands:

```
/agents     # (alias /tasks) list running monitors + background processes
/stop       # stop background processes
```

### Notes

- Both features run in the **gateway** (an always-on agent reacting to external
  events). In a plain interactive CLI session you drive the conversation
  yourself, so there's nothing to inject.
- Both are pure infrastructure — the model never sees a "channel" or "monitor"
  tool. They add zero cost to a session that doesn't use them.
- Delivery falls back to the home channel; configure one with `/sethome` (or
  `gateway` home-channel config) so global events have somewhere to land.
