"""Read-triggered feeder: drain engine outboxes into the spine + reconcile.

The SSE/snapshot read path calls feed() so the spine reflects current engine
state without a background daemon (mirroring oc_agents' reconcile-on-read grain).
It drains each engine's run_outbox into the spine via the single drainer, then
runs the truth-under-failure reconciler over live oc_agents sessions so a
crashed/hung worker surfaces as failed/stalled on the very next read. Every step
is best-effort and defensive: a missing engine or table degrades to a no-op, it
never raises into the request handler.
"""

from __future__ import annotations

from typing import Dict


def feed() -> Dict[str, int]:
    """Drain known engine outboxes into the spine and reconcile liveness.
    Returns a small counts dict for observability. Never raises."""
    counts = {"agents": 0, "teams": 0, "flow": 0, "reconciled": 0}

    from . import drainer

    # oc_agents
    try:
        from plugins.oc_agents import db as agents_db
        counts["agents"] = drainer.drain(agents_db.connect)
    except Exception:
        pass

    # oc_teams (outbox added when that engine emits; absent table -> drainer no-op)
    try:
        from plugins.oc_teams import db as teams_db
        if hasattr(teams_db, "connect"):
            counts["teams"] = drainer.drain(teams_db.connect)
    except Exception:
        pass

    # oc_flow
    try:
        from plugins.oc_flow import db as flow_db
        if hasattr(flow_db, "connect"):
            counts["flow"] = drainer.drain(flow_db.connect)
    except Exception:
        pass

    # Truth-under-failure: flip dead/hung live agent runs on the spine.
    try:
        from . import agents_adapter
        verdicts = agents_adapter.reconcile_agents()
        counts["reconciled"] = sum(1 for v in verdicts if v.action in ("failed", "stalled"))
    except Exception:
        pass

    return counts
