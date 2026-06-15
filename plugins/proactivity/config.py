"""Proactivity config — loaded from the ``proactivity:`` block in config.yaml.

PROTECTED INVARIANT (carried from OpenComputer v1): proactive surfacing is
**default-OFF / consent-gated**. The master ``enabled`` flag defaults to ``False`` —
installing the plugin does NOT start surfacing check-ins until the user opts in
(``hermes proactivity enable`` or ``proactivity.enabled: true``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("hermes.plugins.proactivity.config")

DEFAULTS = {
    "enabled": False,            # INVARIANT: default-OFF, consent-gated
    "push_cap_per_day": 1,
    "quiet_start_hour": 22,
    "quiet_end_hour": 8,
    "cadence_evolution": True,
    "event_ttl_days": 14,
}


@dataclass
class ProactivityConfig:
    enabled: bool = False
    push_cap_per_day: int = 1
    quiet_start_hour: int = 22
    quiet_end_hour: int = 8
    cadence_evolution: bool = True
    event_ttl_days: int = 14

    def in_quiet_hours(self, local_hour: int) -> bool:
        """True when *local_hour* (0-23) falls in the quiet window.

        Handles wrap-around (e.g. 22 -> 8): quiet if hour >= start OR hour < end.
        """
        s, e = self.quiet_start_hour, self.quiet_end_hour
        if s == e:
            return False
        if s < e:
            return s <= local_hour < e
        return local_hour >= s or local_hour < e


def _raw_config() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        block = cfg.get("proactivity", {})
        return block if isinstance(block, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactivity: could not load config.yaml (%s); using defaults", exc)
        return {}


def _b(block: dict, key: str) -> bool:
    try:
        return bool(block.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return bool(DEFAULTS[key])


def _i(block: dict, key: str) -> int:
    try:
        return int(block.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return int(DEFAULTS[key])  # type: ignore[arg-type]


def load_proactivity_config(block: dict | None = None) -> ProactivityConfig:
    block = block if block is not None else _raw_config()
    return ProactivityConfig(
        enabled=_b(block, "enabled"),
        push_cap_per_day=_i(block, "push_cap_per_day"),
        quiet_start_hour=_i(block, "quiet_start_hour"),
        quiet_end_hour=_i(block, "quiet_end_hour"),
        cadence_evolution=_b(block, "cadence_evolution"),
        event_ttl_days=_i(block, "event_ttl_days"),
    )
