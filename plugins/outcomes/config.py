"""Load the outcomes plugin's config from config.yaml (NOT new env vars, per v2 policy).

    outcomes:
      enabled: false        # default OFF — opt-in
      judge_enabled: false  # the aux-LLM judge is opt-in (costs tokens); composite is free
      standing_orders: ""   # optional text the judge weighs a turn against

All keys optional; defaults are conservative (off).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("hermes.plugins.outcomes.config")

DEFAULTS = {
    "enabled": False,
    "judge_enabled": False,
    "standing_orders": "",
}

_STANDING_ORDERS_MAX = 2000  # hard cap on the judge-prompt standing-orders text


@dataclass
class OutcomesPluginConfig:
    enabled: bool
    judge_enabled: bool
    standing_orders: str


def _raw_config() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        block = cfg.get("outcomes", {})
        return block if isinstance(block, dict) else {}
    except Exception as exc:  # noqa: BLE001 — standalone/test or pre-config
        logger.debug("outcomes: could not load config.yaml (%s); using defaults", exc)
        return {}


def load_outcomes_config(block: dict | None = None) -> OutcomesPluginConfig:
    block = block if block is not None else _raw_config()

    def _b(key: str) -> bool:
        try:
            return bool(block.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            return bool(DEFAULTS[key])

    # standing_orders feeds the judge prompt — coerce to a bounded string so a malformed
    # or oversized config value can't bloat the prompt or smuggle structure.
    raw_orders = block.get("standing_orders", DEFAULTS["standing_orders"])
    standing_orders = raw_orders if isinstance(raw_orders, str) else str(raw_orders or "")
    if len(standing_orders) > _STANDING_ORDERS_MAX:
        logger.warning("outcomes: standing_orders > %d chars; truncating", _STANDING_ORDERS_MAX)
        standing_orders = standing_orders[:_STANDING_ORDERS_MAX]

    return OutcomesPluginConfig(
        enabled=_b("enabled"),
        judge_enabled=_b("judge_enabled"),
        standing_orders=standing_orders,
    )
