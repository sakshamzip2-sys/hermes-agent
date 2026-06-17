"""Config for the playbook synthesizer (config.yaml, not env vars).

    playbook_synthesizer:
      enabled: false       # default OFF — opt-in (autonomous skill creation)
      max_per_cycle: 3     # cap new skills synthesized in one cycle
      category: "learned"  # category the synthesized skills land under
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("hermes.plugins.playbook_synthesizer.config")

DEFAULTS = {"enabled": False, "max_per_cycle": 3, "category": "learned"}


@dataclass
class PlaybookConfig:
    enabled: bool
    max_per_cycle: int
    category: str


def load_playbook_config(block: dict | None = None) -> PlaybookConfig:
    if block is None:
        try:
            from hermes_cli.config import load_config

            cfg = load_config() or {}
            b = cfg.get("playbook_synthesizer", {})
            block = b if isinstance(b, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.debug("playbook_synthesizer: config load failed (%s)", exc)
            block = {}

    def _i(key: str) -> int:
        try:
            return int(block.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])  # type: ignore[arg-type]

    return PlaybookConfig(
        enabled=bool(block.get("enabled", DEFAULTS["enabled"])),
        max_per_cycle=_i("max_per_cycle"),
        category=str(block.get("category", DEFAULTS["category"]) or "learned"),
    )
