#!/usr/bin/env python3
"""Live proof: an agent team genuinely coordinating on a shared objective.

Creates a real oc_teams team (shared goal + members + dependent tasks) in the
real teams DB and demonstrates genuine coordination, not just parallelism:
  - dependency gating: a task is NOT claimable until its dependency completes,
  - atomic claim: exactly one member wins a contested task (no double-work),
  - completion unblocks the dependent task.

This complements the 33 oc_teams unit tests (incl the 8-thread atomic-claim
barrier) with a narrated end-to-end run, and leaves the team in the real DB so it
surfaces in the live cockpit's Teams section. Run:
    .venv/bin/python scripts/demo_live_team.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.oc_teams import coordinator, db  # noqa: E402


def _hr(t):
    print(f"\n=== {t} ===")


def main() -> int:
    _hr("1. create a team with a SHARED objective + members")
    team_id = coordinator.create_team(
        name="ship-feature-x",
        goal="Ship feature X: research the approach, then implement it",
        lead_name="lead",
    )
    db.add_member(team_id, "atlas", role="researcher")
    db.add_member(team_id, "coder", role="engineer")
    members = [m["name"] for m in db.list_members(team_id)]
    print(f"team={team_id} goal set; members={members}")

    _hr("2. create two tasks with a real DEPENDENCY (implement depends on research)")
    research = db.create_task(team_id, "Research the approach for feature X", created_by="lead")
    implement = db.create_task(team_id, "Implement feature X", depends_on=[research], created_by="lead")
    print(f"research={research}  implement={implement} (depends_on research)")

    _hr("3. dependency gating: implement is NOT claimable until research is done")
    claimable_now = [t["id"] for t in db.claimable_tasks(team_id)]
    print(f"claimable now: {claimable_now}")
    print(f"  research claimable? {research in claimable_now}  (expected True)")
    print(f"  implement claimable? {implement in claimable_now}  (expected False - blocked by dep)")
    gate_ok = research in claimable_now and implement not in claimable_now

    _hr("4. atomic claim: atlas claims research, coder cannot also claim it")
    a = db.claim_task(research, "atlas")
    b = db.claim_task(research, "coder")
    print(f"atlas claim={a}  coder claim={b}  (expected True/False - exactly one winner)")
    claim_ok = a and not b

    _hr("5. completion UNBLOCKS the dependent task")
    db.complete_task(research, "atlas")
    claimable_after = [t["id"] for t in db.claimable_tasks(team_id)]
    print(f"claimable after research done: {claimable_after}")
    print(f"  implement now claimable? {implement in claimable_after}  (expected True)")
    unblock_ok = implement in claimable_after

    _hr("RESULT")
    summary = db.team_status_summary(team_id)
    print(f"team status summary: {summary}")
    ok = gate_ok and claim_ok and unblock_ok
    print("\nPASS: the team genuinely coordinates (dep gating + atomic claim + unblock)"
          if ok else "FAIL: coordination not demonstrated")
    print(f"(team {team_id} left in the real DB; it now shows in the cockpit Teams section)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
