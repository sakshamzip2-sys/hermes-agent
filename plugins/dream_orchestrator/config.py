"""Load the dream-orchestrator's config from the ``dream_orchestrator:`` block.

Per v2 policy, behavioural settings live in ``config.yaml`` (NOT new env vars).
The block is::

    dream_orchestrator:
      enabled: false            # opt-in; the command works on-demand regardless
      schedule: ""              # cron schedule for the background job ("" = off)
      targets:
        local: true
        honcho: true
        gbrain: true
      cross_feed:
        enabled: false          # Phase 2 one-way import (honcho -> gbrain -> local)
        dry_run: true           # default: only PREVIEW imported candidates
        confidence_floor: high  # only import high-confidence upstream outputs
        max_imports_per_run: 20

All keys are optional; defaults are conservative (everything that touches state
defaults OFF / dry-run).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("hermes.plugins.dream_orchestrator.config")

DEFAULTS = {
    "enabled": False,
    "schedule": "",
    "targets": {"local": True, "honcho": True, "gbrain": True},
    "cross_feed": {
        "enabled": False,
        "dry_run": True,
        "confidence_floor": "high",
        "max_imports_per_run": 20,
    },
}


@dataclass
class CrossFeedConfig:
    enabled: bool = False
    dry_run: bool = True
    confidence_floor: str = "high"
    max_imports_per_run: int = 20


@dataclass
class OrchestratorConfig:
    enabled: bool = False
    schedule: str = ""
    targets: dict[str, bool] = field(default_factory=lambda: dict(DEFAULTS["targets"]))
    cross_feed: CrossFeedConfig = field(default_factory=CrossFeedConfig)


def _raw_config() -> dict:
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        block = cfg.get("dream_orchestrator", {})
        return block if isinstance(block, dict) else {}
    except Exception as exc:  # noqa: BLE001 — standalone/test or pre-config
        logger.debug("dream_orchestrator: could not load config.yaml (%s); defaults", exc)
        return {}


def load_orchestrator_config(block: dict | None = None) -> OrchestratorConfig:
    """Build the config, merging config.yaml over conservative defaults."""
    block = block if block is not None else _raw_config()
    targets_raw = block.get("targets") or {}
    targets = dict(DEFAULTS["targets"])
    if isinstance(targets_raw, dict):
        for k in ("local", "honcho", "gbrain"):
            if k in targets_raw:
                targets[k] = bool(targets_raw[k])

    cf_raw = block.get("cross_feed") or {}
    cf_def = DEFAULTS["cross_feed"]
    cross_feed = CrossFeedConfig(
        enabled=bool(cf_raw.get("enabled", cf_def["enabled"])),
        dry_run=bool(cf_raw.get("dry_run", cf_def["dry_run"])),
        confidence_floor=str(cf_raw.get("confidence_floor", cf_def["confidence_floor"])),
        max_imports_per_run=_safe_int(
            cf_raw.get("max_imports_per_run", cf_def["max_imports_per_run"]),
            cf_def["max_imports_per_run"],
        ),
    )
    return OrchestratorConfig(
        enabled=bool(block.get("enabled", DEFAULTS["enabled"])),
        schedule=str(block.get("schedule", DEFAULTS["schedule"]) or ""),
        targets=targets,
        cross_feed=cross_feed,
    )


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
