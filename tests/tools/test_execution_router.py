"""Tests for the backend-agnostic execution-mode router.

The router decides HOW a task should run — inline | isolated | durable |
scheduled — as a cheap-default, escalate-on-signal POLICY over the sandbox
primitives. It decides a *mode*, never a concrete backend (docker/e2b live
elsewhere), and it explains itself (auditable) instead of emitting a fake
confidence number.
"""

from tools.execution_router import VALID_MODES, RouteDecision, route_execution_mode


def test_trivial_goal_defaults_to_inline():
    d = route_execution_mode("say hello")
    assert isinstance(d, RouteDecision)
    assert d.mode == "inline"
    assert d.basis == "default"
    assert d.suggested_config == {}


def test_quick_check_is_inline():
    d = route_execution_mode("quickly check disk usage on the server")
    assert d.mode == "inline"


def test_long_running_is_durable():
    d = route_execution_mode(
        "Research papers and write a report. Run for up to 4 hours, checkpoint every 30 min."
    )
    assert d.mode == "durable"
    assert d.basis == "signal"
    assert d.suggested_config == {"persist_sandbox_handle": True}
    assert d.reasons  # explainable, not a black box


def test_recurring_is_scheduled():
    d = route_execution_mode(
        "Monitor the production API health every 15 minutes and alert daily"
    )
    assert d.mode == "scheduled"
    assert d.suggested_config == {"use_cron": True}


def test_parallel_isolated_is_isolated():
    d = route_execution_mode(
        "Spawn 5 parallel sub-agents to analyze different repos in isolation"
    )
    assert d.mode == "isolated"
    assert d.suggested_config == {"subagent_sandbox": "isolated"}


def test_explicit_hint_overrides_signals():
    d = route_execution_mode("quick check", hints={"mode": "durable"})
    assert d.mode == "durable"
    assert d.basis == "explicit"


def test_duration_hint_escalates_to_durable():
    d = route_execution_mode("do a thing", hints={"expected_duration_minutes": 120})
    assert d.mode == "durable"
    assert d.duration_hint_minutes == 120


def test_short_duration_hint_stays_inline():
    d = route_execution_mode("do a thing", hints={"expected_duration_minutes": 2})
    assert d.mode == "inline"


def test_scheduled_takes_precedence_over_durable():
    # recurrence is the stronger shape: a recurring long job → cron, not a
    # single resumable session.
    d = route_execution_mode("every day run a 2 hour backup that checkpoints")
    assert d.mode == "scheduled"


def test_durable_takes_precedence_over_isolated():
    d = route_execution_mode("run an isolated long-running resumable job for hours")
    assert d.mode == "durable"


def test_decision_is_backend_agnostic():
    d = route_execution_mode("run for hours in a dedicated sandbox")
    blob = (d.mode + " " + " ".join(d.reasons) + " " + str(d.suggested_config)).lower()
    assert "e2b" not in blob
    assert "docker" not in blob
    assert "firecracker" not in blob


def test_invalid_explicit_mode_falls_back():
    d = route_execution_mode("quick check", hints={"mode": "bogus"})
    assert d.mode in VALID_MODES
    assert d.mode == "inline"  # bogus ignored → falls back to signal/default


def test_as_dict_is_serializable():
    import json

    d = route_execution_mode("run for hours", hints={"expected_duration_minutes": 90})
    blob = json.dumps(d.as_dict())  # must round-trip for logging/audit
    assert '"mode"' in blob and '"reasons"' in blob
