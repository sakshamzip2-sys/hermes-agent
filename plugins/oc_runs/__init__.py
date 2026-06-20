"""oc_runs: the durable, versioned run-event spine for the Parallel Agents view.

A single append-only event log (``oc_runs.db``) that the three engine systems
(oc_agents, oc_teams, oc_flow) and the in-process delegate engine emit into via
an outbox, plus a pure-fold snapshot cache. The cockpit and the orchestrator
both read this one log, so live status, durability, resumable streaming, and
truthful-under-failure reporting all rest on one primitive instead of six
mismatched enums. See agents-mission/03-design-parallel-agents.md.
"""
