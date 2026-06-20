"""spawn_guarded: the single atomic choke point for every spawn.

All caps are enforced here and nowhere else. A reservation is taken in ONE
``BEGIN IMMEDIATE`` compare-and-swap transaction so the counted resource (a
``slot_reservations`` row) and the lock (the write transaction) are the same
row in the same DB. Two concurrent admits therefore cannot both pass the count
check, which makes runaway fan-out impossible by construction across every
spawn channel (oc_agents, oc_teams, oc_flow, in-process delegate), with no
lost-update window. Budget is a HARD pre-spend debit in the same transaction, so
it cannot lag and overspend.

Configured caps are clamped to hard ceilings the config cannot exceed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from . import db as odb

# Defaults (overridable via config / ensure_tree). See 03-design-orchestrator.md.
DEFAULT_CAPS: Dict[str, int] = {
    "max_depth": 3,
    "max_workers_per_parent": 5,
    "max_concurrent": 32,
    "max_fanout": 10,
    "max_spawns": 200,
}

# Hard ceilings the configured caps may never exceed.
HARD_CEILINGS: Dict[str, int] = {
    "max_depth": 5,
    "max_workers_per_parent": 10,
    "max_concurrent": 64,
    "max_fanout": 10,
}


@dataclass
class SpawnDecision:
    ok: bool
    reservation_id: Optional[str] = None
    refused_cap: str = ""
    limit: int = 0
    requested: int = 0


def _resolved_caps(overrides: Optional[Dict[str, int]]) -> Dict[str, int]:
    caps = dict(DEFAULT_CAPS)
    if overrides:
        caps.update({k: int(v) for k, v in overrides.items() if k in caps})
    for k, ceiling in HARD_CEILINGS.items():
        caps[k] = min(caps[k], ceiling)
    return caps


def ensure_tree(
    conn,
    run_tree_id: str,
    *,
    caps_overrides: Optional[Dict[str, int]] = None,
    budget_usd: Optional[float] = None,
) -> None:
    """Create the run-tree lease row if absent (idempotent). Caps are clamped to
    the hard ceilings."""
    caps = _resolved_caps(caps_overrides)
    conn.execute(
        """INSERT OR IGNORE INTO run_leases
           (run_tree_id, max_depth, max_workers_per_parent, max_concurrent,
            max_fanout, max_spawns, budget_usd, spawns_total, created_at)
           VALUES (?,?,?,?,?,?,?,0,?)""",
        (run_tree_id, caps["max_depth"], caps["max_workers_per_parent"],
         caps["max_concurrent"], caps["max_fanout"], caps["max_spawns"],
         budget_usd, odb.now()),
    )


def _active_count(conn, run_tree_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM slot_reservations WHERE run_tree_id=? AND status='reserved'",
        (run_tree_id,),
    ).fetchone()
    return int(row["c"]) if row else 0


def reserve_slot_locked(
    conn,
    run_tree_id: str,
    *,
    parent_node: str = "",
    depth: int = 0,
    est_usd: float = 0.0,
    fanout_size: int = 1,
) -> SpawnDecision:
    """Core cap check + reservation write. ASSUMES the caller already holds an
    open write transaction (BEGIN IMMEDIATE) and will COMMIT/ROLLBACK. On
    success it writes the reservation and debits the lease; on a cap breach it
    writes nothing and returns the refusal. Lets recovery reserve a slot inside
    its own claim transaction without nesting transactions."""
    lease = conn.execute(
        "SELECT * FROM run_leases WHERE run_tree_id=?", (run_tree_id,)
    ).fetchone()
    if lease is None:
        return SpawnDecision(False, refused_cap="no_lease")

    if depth > lease["max_depth"]:
        return SpawnDecision(False, refused_cap="depth", limit=lease["max_depth"], requested=depth)
    if fanout_size > lease["max_fanout"]:
        return SpawnDecision(False, refused_cap="fanout", limit=lease["max_fanout"], requested=fanout_size)
    if lease["spawns_total"] >= lease["max_spawns"]:
        return SpawnDecision(False, refused_cap="max_spawns", limit=lease["max_spawns"],
                             requested=lease["spawns_total"] + 1)
    active = _active_count(conn, run_tree_id)
    if active >= lease["max_concurrent"]:
        return SpawnDecision(False, refused_cap="concurrent", limit=lease["max_concurrent"],
                             requested=active + 1)
    if lease["budget_usd"] is not None and (lease["budget_usd"] - est_usd) < 0:
        return SpawnDecision(False, refused_cap="budget", limit=int(lease["budget_usd"]),
                             requested=int(est_usd))

    rid = odb.new_id()
    conn.execute(
        """INSERT INTO slot_reservations
           (id, run_tree_id, parent_node, depth, est_usd, status, created_at)
           VALUES (?,?,?,?,?, 'reserved', ?)""",
        (rid, run_tree_id, parent_node, depth, est_usd, odb.now()),
    )
    conn.execute(
        """UPDATE run_leases SET spawns_total = spawns_total + 1,
           budget_usd = CASE WHEN budget_usd IS NULL THEN NULL ELSE budget_usd - ? END
           WHERE run_tree_id=?""",
        (est_usd, run_tree_id),
    )
    return SpawnDecision(True, reservation_id=rid)


def spawn_guarded(
    conn,
    run_tree_id: str,
    *,
    parent_node: str = "",
    depth: int = 0,
    est_usd: float = 0.0,
    fanout_size: int = 1,
) -> SpawnDecision:
    """Reserve one spawn slot under all caps, atomically. Returns a decision; the
    caller MUST handle a refusal (never spawn on ``ok is False``)."""
    ensure_tree(conn, run_tree_id)  # robust: defaults if not pre-created
    conn.execute("BEGIN IMMEDIATE")
    try:
        d = reserve_slot_locked(conn, run_tree_id, parent_node=parent_node, depth=depth,
                                est_usd=est_usd, fanout_size=fanout_size)
        if d.ok:
            odb.record_decision(conn, run_tree_id, "spawn",
                                {"reservation_id": d.reservation_id, "depth": depth,
                                 "est_usd": est_usd, "parent_node": parent_node})
            conn.execute("COMMIT")
            return d
        conn.execute("ROLLBACK")
        odb.record_decision(conn, run_tree_id, "cap_exceeded",
                            {"cap": d.refused_cap, "limit": d.limit,
                             "requested": d.requested, "depth": depth})
        return d
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def release(conn, reservation_id: Optional[str]) -> bool:
    """Free a reserved concurrency slot on a terminal event. Does NOT decrement
    the monotonic spawns_total backstop. Idempotent."""
    if not reservation_id:
        return False
    cur = conn.execute(
        "UPDATE slot_reservations SET status='released', released_at=? "
        "WHERE id=? AND status='reserved'",
        (odb.now(), reservation_id),
    )
    return cur.rowcount > 0
