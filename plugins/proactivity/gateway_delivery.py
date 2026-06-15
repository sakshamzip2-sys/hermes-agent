"""Deliver proactive pushes/digests OUT through the gateway.

Reuses v2's proven cron outbound path (`cron.scheduler._deliver_result` →
live `adapter.send` or the standalone platform sender), so a proactive message reaches
the user's configured home channel(s) exactly like a cron job's output does.

Everything is best-effort and fail-soft: if the gateway/delivery path isn't available
(e.g. pure CLI, no home channel configured), delivery returns False and the caller keeps
the moment queued for the digest rather than losing it.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger("hermes.plugins.proactivity.gateway_delivery")

# Injectable seam for tests. Signature mirrors cron.scheduler._deliver_result:
#   (job: dict, content: str, *, adapters=None, loop=None) -> error_str_or_None
_deliver_fn: Optional[Callable] = None


def _resolve_deliver_fn() -> Optional[Callable]:
    if _deliver_fn is not None:
        return _deliver_fn
    try:
        from cron.scheduler import _deliver_result

        return _deliver_result
    except Exception as exc:  # noqa: BLE001 — not in a gateway-capable environment
        logger.debug("proactivity: cron delivery unavailable (%s)", exc)
        return None


def set_deliver_fn(fn: Optional[Callable]) -> None:
    """Test seam — override the underlying delivery function."""
    global _deliver_fn
    _deliver_fn = fn


def deliver(text: str, *, target: str = "all", adapters=None, loop=None) -> bool:
    """Deliver *text* to the user's home channel(s) through the gateway.

    ``target`` follows cron's delivery-target grammar ("all" = every configured home
    channel, or e.g. "telegram"). Returns True on success.
    """
    text = (text or "").strip()
    if not text:
        return False
    fn = _resolve_deliver_fn()
    if fn is None:
        return False
    job = {"id": "proactivity_push", "deliver": target}
    try:
        err = fn(job, text, adapters=adapters, loop=loop)
    except TypeError:
        # Older/newer signature without kwargs — try positionally.
        try:
            err = fn(job, text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("proactivity: delivery call failed (%s)", exc)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.debug("proactivity: delivery failed (%s)", exc)
        return False
    if err:
        logger.debug("proactivity: delivery reported error: %s", err)
        return False
    return True
