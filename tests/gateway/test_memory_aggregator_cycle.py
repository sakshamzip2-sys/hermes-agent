"""Regression: GBrain dream-cycle fields must normalize to STRINGS, not objects.

GBrain's get_status_snapshot started returning cycle.last_full as a full report object
({name, status, duration_ms, totals, finished_at}); the old code passed it straight to
the frontend, which rendered it as a React child → the whole Memory tab crashed.
"""

from __future__ import annotations

from gateway.platforms.memory_aggregator import _cycle_timestamp


def test_object_cycle_extracts_finished_at() -> None:
    obj = {"name": "autopilot-cycle", "status": "completed", "duration_ms": 127,
           "finished_at": "2026-06-17T13:00:00Z", "totals": {"orphans_found": 1}}
    assert _cycle_timestamp(obj) == "2026-06-17T13:00:00Z"


def test_object_without_finished_at_falls_back_to_status() -> None:
    obj = {"name": "autopilot-cycle", "status": "completed", "duration_ms": 5}
    # No timestamp → a readable string, never the object itself.
    out = _cycle_timestamp(obj)
    assert isinstance(out, str)
    assert "completed" in out


def test_string_cycle_passes_through() -> None:
    assert _cycle_timestamp("2026-06-17T13:00:00Z") == "2026-06-17T13:00:00Z"


def test_none_stays_none() -> None:
    assert _cycle_timestamp(None) is None


def test_unexpected_type_coerces_to_string_or_none() -> None:
    # Lists/numbers must never leak through as a non-string object.
    out = _cycle_timestamp([1, 2, 3])
    assert out is None or isinstance(out, str)
