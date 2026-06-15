"""Load the dreaming plugin's behavioural config from config.yaml.

Per v2 policy, behavioural settings live in ``config.yaml`` (NOT new env vars).
The block is::

    dreaming:
      enabled: true
      min_interval_hours: 6        # opportunistic-trigger debounce
      score_threshold: 0.65
      min_recall_count: 2
      diversity_threshold: 0.8
      max_promotions_per_run: 20
      dreams_md_max_bytes: 16384
      candidate_fetch_limit: 50
      supersede_enabled: true
      recall_gate_enabled: true

All keys are optional; defaults match OpenComputer v1.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .engine import DreamingConfig

logger = logging.getLogger("hermes.plugins.dreaming.config")

DEFAULTS = {
    "enabled": True,
    "min_interval_hours": 6.0,
    "score_threshold": 0.65,
    "min_recall_count": 2,
    "diversity_threshold": 0.8,
    "max_promotions_per_run": 20,
    "dreams_md_max_bytes": 16384,
    "candidate_fetch_limit": 50,
    "supersede_enabled": True,
    "recall_gate_enabled": True,
    "cluster_similarity_threshold": 0.7,
}


@dataclass
class DreamingPluginConfig:
    enabled: bool
    min_interval_hours: float
    candidate_fetch_limit: int
    engine: DreamingConfig
    cluster_similarity_threshold: float = 0.7

    @property
    def min_interval_seconds(self) -> float:
        return max(0.0, self.min_interval_hours * 3600.0)


def _raw_config() -> dict:
    """Read the ``dreaming`` block from v2 config, or {} if unavailable."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        block = cfg.get("dreaming", {})
        return block if isinstance(block, dict) else {}
    except Exception as exc:  # noqa: BLE001 — standalone/test or pre-config
        logger.debug("dreaming: could not load config.yaml (%s); using defaults", exc)
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


def _f(block: dict, key: str) -> float:
    try:
        return float(block.get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])  # type: ignore[arg-type]


def load_dreaming_config(block: dict | None = None) -> DreamingPluginConfig:
    """Build the plugin config, merging config.yaml over the v1-matching defaults."""
    block = block if block is not None else _raw_config()
    return DreamingPluginConfig(
        enabled=_b(block, "enabled"),
        min_interval_hours=_f(block, "min_interval_hours"),
        candidate_fetch_limit=_i(block, "candidate_fetch_limit"),
        cluster_similarity_threshold=_f(block, "cluster_similarity_threshold"),
        engine=DreamingConfig(
            enabled=_b(block, "enabled"),
            score_threshold=_f(block, "score_threshold"),
            min_recall_count=_i(block, "min_recall_count"),
            diversity_threshold=_f(block, "diversity_threshold"),
            max_promotions_per_run=_i(block, "max_promotions_per_run"),
            dreams_md_max_bytes=_i(block, "dreams_md_max_bytes"),
            candidate_fetch_limit=_i(block, "candidate_fetch_limit"),
            supersede_enabled=_b(block, "supersede_enabled"),
            recall_gate_enabled=_b(block, "recall_gate_enabled"),
        ),
    )
