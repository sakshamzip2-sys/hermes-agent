"""Config for the self-evolution conductor (config.yaml, not env vars).

    self_evolution:
      enabled: false     # the SCHEDULED cron trigger is opt-in (the command always works)
      schedule: ""       # e.g. "every 6 hours" — registers the nightly cron when set
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("hermes.plugins.self_evolution.config")


@dataclass
class SelfEvolutionConfig:
    enabled: bool = False
    schedule: str = ""
    feed_up: bool = False
    """ENRICH↑: push per-session outcome scores up to GBrain each cycle. Opt-in —
    it writes to an external engine."""


def load_self_evolution_config(block: dict | None = None) -> SelfEvolutionConfig:
    if block is None:
        try:
            from hermes_cli.config import load_config

            cfg = load_config() or {}
            b = cfg.get("self_evolution", {})
            block = b if isinstance(b, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("self_evolution: config load failed (%s)", exc)
            block = {}
    return SelfEvolutionConfig(
        enabled=bool(block.get("enabled", False)),
        schedule=str(block.get("schedule", "") or ""),
        feed_up=bool(block.get("feed_up", False)),
    )
