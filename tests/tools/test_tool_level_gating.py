"""Tool-level destructive gating (2026-06-21).

The terminal approval path only covered shell commands; a direct destructive
TOOL call (e.g. cronjob remove) used to fire with no confirmation. These tests
lock the fix: extract_target surfaces an action verb, an ask rule like
``cronjob(remove)`` matches that verb, and check_tool_approval never hangs
outside a gateway context.
"""

import tools.permission_rules as pr
from tools.approval import check_tool_approval, _describe_tool_call


def _policy(*, mode="normal", allow=(), deny=(), ask=()):
    return pr.build_policy(
        {"mode": mode, "allow": list(allow), "deny": list(deny), "ask": list(ask)}
    )


class TestActionTargetExtraction:
    def test_surfaces_action_verb(self):
        assert pr.extract_target("cronjob", {"action": "remove", "id": "x"}) == "remove"
        assert pr.extract_target("cronjob", {"action": "list"}) == "list"
        assert pr.extract_target("skill_manage", {"action": "delete"}) == "delete"
        assert pr.extract_target("memory", {"operation": "remove"}) == "remove"

    def test_no_action_arg_returns_empty(self):
        assert pr.extract_target("cronjob", {"id": "x"}) == ""
        assert pr.extract_target("cronjob", None) == ""


class TestToolVerbGating:
    def test_destructive_verb_asks(self):
        pol = _policy(ask=["cronjob(remove)"])
        assert pr.decide(pol, "cronjob", {"action": "remove", "id": "m"}).action == "ask"

    def test_safe_verb_is_normal(self):
        pol = _policy(ask=["cronjob(remove)"])
        assert pr.decide(pol, "cronjob", {"action": "list"}).action == "normal"

    def test_skill_manage_delete_asks_but_view_does_not(self):
        pol = _policy(ask=["skill_manage(delete)", "skill_manage(remove_file)"])
        assert pr.decide(pol, "skill_manage", {"action": "delete"}).action == "ask"
        assert pr.decide(pol, "skill_manage", {"action": "view"}).action == "normal"

    def test_memory_remove_asks_but_recall_does_not(self):
        pol = _policy(ask=["memory(remove)"])
        assert pr.decide(pol, "memory", {"action": "remove"}).action == "ask"
        assert pr.decide(pol, "memory", {"action": "recall"}).action == "normal"

    def test_deny_still_beats_ask(self):
        pol = _policy(deny=["cronjob(remove)"], ask=["cronjob(remove)"])
        assert pr.decide(pol, "cronjob", {"action": "remove"}).action == "deny"


class TestCheckToolApprovalNoHang:
    def test_returns_approved_outside_gateway_context(self, monkeypatch):
        # No gateway/cron env -> must approve immediately (never block a headless run).
        monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
        monkeypatch.delenv("HERMES_CRON_SESSION", raising=False)
        r = check_tool_approval("cronjob", {"action": "remove", "id": "m"}, "default")
        assert r["approved"] is True
        assert r["message"] is None

    def test_cron_session_never_blocks(self, monkeypatch):
        # Cron is explicitly excluded from interactive gateway approval.
        monkeypatch.setenv("HERMES_CRON_SESSION", "1")
        r = check_tool_approval("cronjob", {"action": "remove", "id": "m"}, "default")
        assert r["approved"] is True


class TestDescribeToolCall:
    def test_compact_summary(self):
        assert _describe_tool_call("cronjob", {"action": "remove", "id": "morning"}) == (
            "cronjob remove morning"
        )
        assert _describe_tool_call("skill_manage", {"action": "delete", "name": "x"}) == (
            "skill_manage delete x"
        )
