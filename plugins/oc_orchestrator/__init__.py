"""oc_orchestrator: the supervisory control plane over the agent fleet.

A bounded hierarchy of identical supervisor nodes running deterministic mechanism
code (cap enforcement, idempotent recovery, liveness/stall detection, the driver
tick) with a thin advisory brain consulted only at ambiguous decision points.

Built strictly ON the Feature B substrate (the oc_runs spine + reconciler) and
the oc_agents/oc_teams/oc_flow spawn seams, consumed by capability. Caps are
enforced at ONE choke point (caps.spawn_guarded) backed by ONE atomic
reservation ledger, so runaway fan-out is impossible by construction. Recovery is
intent-then-execute, so a crash mid-spawn neither double-spawns nor abandons a
task. See agents-mission/03-design-orchestrator.md.
"""
