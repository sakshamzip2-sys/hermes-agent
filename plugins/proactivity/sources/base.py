"""The ProactiveSource interface + a fail-soft runner.

A source observes one kind of signal and emits candidate ``ProactiveMoment``s. It is
deliberately dumb: it does NOT decide whether/when/how to deliver — that is the gate's
job. Sources may set urgency/sensitivity/confidence hints, nothing more.

``run_sources`` runs every available source, isolating failures so one broken source
never blocks the others (v1's proven discovery pattern).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from ..moment import ProactiveMoment

logger = logging.getLogger("hermes.plugins.proactivity.sources")


@dataclass
class PollContext:
    """Everything a source needs to observe its signal."""

    now: float
    home: Path                       # profile proactivity dir (for source-local state)
    state_db: Optional[Path] = None  # v2 state.db (conversation history), if resolvable
    recent_window_seconds: float = 7 * 24 * 3600.0  # how far back "recent" looks
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class ProactiveSource(Protocol):
    id: str

    def available(self) -> bool:
        """Cheap check — is this source usable in the current environment?"""
        ...

    async def poll(self, ctx: PollContext) -> list[ProactiveMoment]:
        """Emit candidate moments. Must be fail-soft and side-effect-light."""
        ...


async def run_sources(sources: list, ctx: PollContext) -> list[ProactiveMoment]:
    """Run every available source, isolating per-source failures.

    Returns the flattened list of candidate moments. A source that is unavailable or
    raises contributes nothing but never blocks the others.
    """
    out: list[ProactiveMoment] = []
    for src in sources:
        sid = getattr(src, "id", type(src).__name__)
        try:
            if not src.available():
                continue
            moments = await src.poll(ctx)
        except Exception as exc:  # noqa: BLE001 — one source must never break the sweep
            logger.warning("proactivity: source %s.poll raised %s: %s", sid, type(exc).__name__, exc)
            continue
        for m in moments or []:
            m.ensure_dedup_key()
            out.append(m)
    return out
