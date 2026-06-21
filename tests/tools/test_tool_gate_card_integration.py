"""End-to-end integration proof of the tool-level approval CARD wiring (2026-06-21).

Proves the FULL path the live web UI exercises:
  central pre-tool gate (get_pre_tool_call_block_message)
    -> check_tool_approval (gateway context)
      -> _await_gateway_decision -> notify_cb fires  [== the web UI emits the
         approval.requested SSE that renders the ApprovalRenderer card]
      -> user resolves (resolve_gateway_approval) -> gate returns allow/deny.

Deterministic equivalent of clicking "Approve once"/"Deny" on the card, immune
to model/router flakiness. Note: get_pre_tool_call_block_message takes
session_id as the 4th arg (3rd is task_id), so it MUST be passed by keyword -
exactly as the production caller does (agent/tool_executor.py).
"""

import threading
import time

import tools.approval as ap
import tools.permission_rules as pr
from hermes_cli.plugins import get_pre_tool_call_block_message

SK = "test-gate-card-session"


def _setup(monkeypatch, choice, captured):
    """Interactive gateway context + a registered approval channel whose button
    click (choice) is auto-driven, simulating the user resolving the card."""
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    # Inject the ask policy directly (the test conftest redirects HERMES_HOME to
    # a fake home with no config.yaml, so we cannot rely on the live ask rules).
    pr.set_runtime_rules(ask=["cronjob(remove)"])
    # Pin the resolved session key so the channel we register matches what
    # check_tool_approval looks up (production binds this via contextvar pre-run).
    monkeypatch.setattr(ap, "get_current_session_key", lambda default="default": SK)

    def cb(approval_data):
        captured["card"] = approval_data
        def _resolve():
            time.sleep(0.3)
            captured["resolved"] = ap.resolve_gateway_approval(SK, choice)
        threading.Thread(target=_resolve, daemon=True).start()

    ap.register_gateway_notify(SK, cb)


def test_destructive_tool_emits_card_then_allows_on_approve(monkeypatch):
    captured = {}
    _setup(monkeypatch, "once", captured)
    try:
        block = get_pre_tool_call_block_message(
            "cronjob", {"action": "remove", "id": "b989fd112482"}, session_id=SK
        )
        assert "card" in captured, "approval card was NOT emitted for a destructive tool call"
        assert "cronjob" in captured["card"]["command"]
        assert "remove" in captured["card"]["command"]
        assert captured.get("resolved") == 1
        assert block is None  # approved -> tool proceeds
    finally:
        ap.unregister_gateway_notify(SK)
        pr.clear_runtime_rules()


def test_destructive_tool_blocks_on_deny(monkeypatch):
    captured = {}
    _setup(monkeypatch, "deny", captured)
    try:
        block = get_pre_tool_call_block_message(
            "cronjob", {"action": "remove", "id": "x"}, session_id=SK
        )
        assert "card" in captured, "approval card was NOT emitted"
        assert block is not None and "BLOCKED" in block  # denied -> tool blocked
    finally:
        ap.unregister_gateway_notify(SK)
        pr.clear_runtime_rules()


def test_safe_verb_runs_without_card(monkeypatch):
    captured = {}
    _setup(monkeypatch, "once", captured)
    try:
        block = get_pre_tool_call_block_message(
            "cronjob", {"action": "list"}, session_id=SK
        )
        assert "card" not in captured, "read-only verb should NOT trigger a card"
        assert block is None
    finally:
        ap.unregister_gateway_notify(SK)
        pr.clear_runtime_rules()


def test_no_card_outside_gateway_context(monkeypatch):
    """CLI/cron (no gateway channel) must never block on a card."""
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
    block = get_pre_tool_call_block_message(
        "cronjob", {"action": "remove", "id": "x"}, session_id="no-gateway"
    )
    assert block is None
