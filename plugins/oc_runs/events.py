"""The frozen, versioned run-event envelope and its vocabulary.

The envelope is stable and carries ``schema_version`` on every event. The type
and source vocabularies are OPEN: consumers must ignore unknown ``type`` and
unknown ``source`` rather than treating them as errors (guardrail 9), so adding
an event kind later never breaks an older reader. The constants below are the
known kinds at schema version 1; emitting a string outside this set is allowed.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

# Bump only on a breaking envelope change. Additive payload keys do NOT bump it.
SCHEMA_VERSION = 1

# Known sources (the engine or watchdog that produced the event). Open vocab.
SOURCE_AGENTS = "agents"
SOURCE_TEAMS = "teams"
SOURCE_FLOW = "flow"
SOURCE_DELEGATE = "delegate"
SOURCE_RECONCILER = "reconciler"
SOURCE_ORCHESTRATOR = "orchestrator"

# Known event types at schema v1. Open vocab (a future kind is data, not error).
RUN_CREATED = "run.created"
RUN_STATUS = "run.status"
RUN_PROGRESS = "run.progress"
TOOL_STARTED = "tool.started"
TOOL_COMPLETED = "tool.completed"
NEEDS_INPUT = "needs_input"
DEP_BLOCKED = "dep.blocked"
DEP_UNBLOCKED = "dep.unblocked"
TEAM_MESSAGE = "team.message"
HEARTBEAT = "heartbeat"
RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_STALLED = "run.stalled"
SNAPSHOT = "snapshot"

# Terminal types: a run carrying one of these is finished and its ledger slot,
# if any, is releasable. Used by the reconciler and the projection fold.
TERMINAL_TYPES = frozenset({RUN_COMPLETED, RUN_FAILED, RUN_STALLED})


def build_event(
    run_id: str,
    event_type: str,
    *,
    source: str,
    parent_run_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    team_id: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    dedupe_key: Optional[str] = None,
    ts: Optional[float] = None,
) -> Dict[str, Any]:
    """Build one event envelope. ``seq`` is intentionally absent: it is assigned
    by the single writer at append time so it is globally monotonic and durable.

    ``dedupe_key`` makes an emit idempotent within a ``run_id`` (a re-emitted
    terminal from two reconcilers collapses to one row). Leave it None for
    events that should always append (progress, heartbeats).
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": ts if ts is not None else time.time(),
        "run_id": run_id,
        "parent_run_id": parent_run_id,
        "source": source,
        "type": event_type,
        "agent_id": agent_id,
        "team_id": team_id,
        "payload": dict(payload) if payload else {},
        "dedupe_key": dedupe_key,
    }
