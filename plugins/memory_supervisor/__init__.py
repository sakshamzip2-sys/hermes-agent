"""Runtime Memory Supervisor (RMS) - req #12a / GAP-3.

A self-contained, opt-in v2 edge plugin that supervises the four memory stores
and the background memory jobs so the agent never SILENTLY degrades when a store
is down.  It fixes the verified Phase-1 condition: today ``is_available()`` is
config-only, so Honcho/GBrain being down produces zero signal, the recall
fan-out eats a dead store's full timeout, and a failed external write vanishes.

Invariants (load-bearing):

* Recall FAILS OPEN: a down store is skipped fast via its circuit breaker and
  never blocks or breaks a turn.
* Writes FAIL CLOSED: a write to a down store is durably QUEUED (never dropped)
  and retried with capped exponential backoff + full jitter; terminal failures
  dead-letter (still in the DB).
* The supervisor never cascade-fails the agent: every loop tick and external
  call is individually wrapped; absence of the plugin equals today's behavior.

State lives in ``$HERMES_HOME/mem_supervisor.db`` (WAL, busy_timeout), so a
gateway restart loses nothing.  Build this wave is self-contained in this
package + a gateway start hook; it does NOT edit ``agent/memory_manager.py`` or
``agent/memory_merge.py`` (that wiring is a separate follow-up).

Public surface (see ``registry`` and ``control_loop``):

    from plugins.memory_supervisor import (
        start_memory_supervisor, stop_memory_supervisor,
        get_memory_supervisor, is_running,
    )
"""

from __future__ import annotations

from .breaker import (
    BreakerConfig,
    BreakerRegistry,
    BreakerSnapshot,
    BreakerState,
    CircuitBreaker,
    FailureClass,
    classify_failure,
)
from .control_loop import MemorySupervisor, SupervisorConfig
from .registry import (
    get_memory_supervisor,
    is_running,
    start_memory_supervisor,
    stop_memory_supervisor,
)

__all__ = [
    "BreakerConfig",
    "BreakerRegistry",
    "BreakerSnapshot",
    "BreakerState",
    "CircuitBreaker",
    "FailureClass",
    "classify_failure",
    "MemorySupervisor",
    "SupervisorConfig",
    "get_memory_supervisor",
    "is_running",
    "start_memory_supervisor",
    "stop_memory_supervisor",
]
