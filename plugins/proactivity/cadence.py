"""Self-evolving cadence — ported from OpenComputer v1.

Reminder frequency adapts to the user via a rolling signal -> dead-band step ->
atomic persist. The hard ceiling (``CADENCE_MAX_PUSH_CAP``) is the safety: auto-tuning
can never push the user more than this, regardless of signal. Everything fail-soft.
Subtractive-only learning: muting only ever suppresses (INVARIANT 3).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The true upper bound on auto-tuned daily pushes. No signal can raise the cap
# above this — the user can never be spammed by evolution.
CADENCE_MAX_PUSH_CAP = 5

_FILE = "proactivity_tuning.json"
_SCHEMA = 1


@dataclass(frozen=True)
class CadenceTuning:
    push_cap: Optional[int] = None           # None -> use the config value (un-evolved)
    muted_keywords: tuple[str, ...] = ()     # NL-added mute keywords (substring match)
    decisions: int = 0                       # how many feedback signals observed
    last_recompute_at: float = 0.0


def keyword_muted(source: str, title: str, keywords: tuple[str, ...]) -> bool:
    """True if any NL-mute keyword is a substring of the event's source or title."""
    if not keywords:
        return False
    hay = f"{source} {title}".lower()
    return any(k.lower() in hay for k in keywords if k)


def step_cap(cap: int, direction: str, *, ceiling: int) -> int:
    """Move the cap one step, bounded by [0, min(ceiling, CADENCE_MAX_PUSH_CAP)]."""
    hard = min(ceiling, CADENCE_MAX_PUSH_CAP)
    if direction == "up":
        return min(hard, cap + 1)
    if direction == "down":
        return max(0, cap - 1)
    return cap


def effective_push_cap(config_cap: int, tuning: CadenceTuning) -> int:
    """The cap the tick should use: the tuned value (or config when un-evolved),
    clamped to the hard max so evolution can never over-push."""
    base = config_cap if tuning.push_cap is None else tuning.push_cap
    return max(0, min(int(base), CADENCE_MAX_PUSH_CAP))


def load_cadence(home: Path | str) -> CadenceTuning:
    """Read the tuning state. Fail-soft -> defaults on any error/absence."""
    try:
        p = Path(home) / _FILE
        if not p.exists():
            return CadenceTuning()
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return CadenceTuning()
        cap = d.get("push_cap")
        return CadenceTuning(
            push_cap=int(cap) if isinstance(cap, int) else None,
            muted_keywords=tuple(d.get("muted_keywords") or ()),
            decisions=int(d.get("decisions") or 0),
            last_recompute_at=float(d.get("last_recompute_at") or 0.0),
        )
    except Exception:  # noqa: BLE001 — fail-soft
        return CadenceTuning()


def save_cadence(home: Path | str, tuning: CadenceTuning) -> None:
    """Atomically persist the tuning state. Fail-soft — never raises."""
    try:
        root = Path(home)
        root.mkdir(parents=True, exist_ok=True)
        p = root / _FILE
        payload = {
            "schema_version": _SCHEMA,
            "push_cap": tuning.push_cap,
            "muted_keywords": list(tuning.muted_keywords),
            "decisions": tuning.decisions,
            "last_recompute_at": tuning.last_recompute_at,
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, p)  # atomic
    except Exception:  # noqa: BLE001 — fail-soft
        pass
