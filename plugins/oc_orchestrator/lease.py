"""Leader-leased fencing token for the single logical driver.

The driver must be a singleton: two gateway processes (or a backstop re-arm
against a wedged-but-alive gateway) must not both tick. A lease row in
``driver_lease`` is acquired before ticking and renewed each tick. A second
holder cannot acquire while the lease is live; a stale lease (past its TTL) is
taken over and the fencing token is bumped, so a resurrected old leader holding
a stale token can be detected and fenced off. The acquire/renew/takeover decision
is one BEGIN IMMEDIATE compare-and-swap, so two racing acquirers cannot both win.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import db as odb


@dataclass
class Lease:
    acquired: bool
    fencing_token: int
    holder: str
    expires_at: float


def acquire_or_renew(conn, holder: str, *, ttl: float = 30.0, now: Optional[float] = None) -> Lease:
    """Acquire the driver lease, renew it if we already hold it, or take over a
    stale one (bumping the fencing token). Returns acquired=False if it is held
    live by someone else."""
    now = now if now is not None else odb.now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT holder, fencing_token, expires_at FROM driver_lease WHERE id=1"
        ).fetchone()

        if row is None:
            token = 1
            conn.execute(
                "INSERT INTO driver_lease (id, holder, fencing_token, expires_at) VALUES (1,?,?,?)",
                (holder, token, now + ttl),
            )
            conn.execute("COMMIT")
            return Lease(True, token, holder, now + ttl)

        cur_holder = row["holder"]
        token = int(row["fencing_token"])
        expires = float(row["expires_at"])

        if cur_holder == holder:
            conn.execute("UPDATE driver_lease SET expires_at=? WHERE id=1", (now + ttl,))
            conn.execute("COMMIT")
            return Lease(True, token, holder, now + ttl)

        if now >= expires:
            new_token = token + 1  # fence the previous holder
            conn.execute(
                "UPDATE driver_lease SET holder=?, fencing_token=?, expires_at=? WHERE id=1",
                (holder, new_token, now + ttl),
            )
            conn.execute("COMMIT")
            return Lease(True, new_token, holder, now + ttl)

        # Held live by someone else.
        conn.execute("ROLLBACK")
        return Lease(False, token, cur_holder, expires)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def release(conn, holder: str) -> None:
    """Release the lease if we hold it (sets it immediately expirable)."""
    conn.execute("UPDATE driver_lease SET expires_at=0 WHERE id=1 AND holder=?", (holder,))
