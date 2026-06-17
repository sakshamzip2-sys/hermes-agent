"""Tests for the outcomes hook glue (success detection + dispatch with a fake engine)."""

from __future__ import annotations

from plugins.outcomes import hooks


def test_tool_success_detection() -> None:
    assert hooks.tool_call_succeeded(status="success", error_type=None) is True
    assert hooks.tool_call_succeeded(status=None, error_type=None) is True
    assert hooks.tool_call_succeeded(status="error", error_type=None) is False
    assert hooks.tool_call_succeeded(status="failed", error_type=None) is False
    assert hooks.tool_call_succeeded(status="success", error_type="ValueError") is False
    assert hooks.tool_call_succeeded(status=None, error_type="Timeout") is False


class _FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def record_tool(self, session_id, *, success):  # noqa: ANN001
        self.calls.append(("record_tool", session_id, success))

    def resolve_pending(self, session_id, *, user_followup=None, judge_fn=None, now=None):  # noqa: ANN001
        self.calls.append(("resolve_pending", session_id, user_followup))
        return 0.5

    def stage_turn(self, session_id, turn, *, trajectory_summary=""):  # noqa: ANN001
        self.calls.append(("stage_turn", session_id, turn))

    def flush_pending(self, session_id, *, judge_fn=None, now=None):  # noqa: ANN001
        self.calls.append(("flush_pending", session_id))
        return 0.5


def test_post_tool_call_records_success() -> None:
    eng = _FakeEngine()
    cb = hooks.make_post_tool_call(eng)
    cb(session_id="S", status="success", error_type=None, function_name="Read")
    assert eng.calls == [("record_tool", "S", True)]


def test_post_tool_call_records_failure() -> None:
    eng = _FakeEngine()
    cb = hooks.make_post_tool_call(eng)
    cb(session_id="S", status="error", error_type="Boom", function_name="Bash")
    assert eng.calls == [("record_tool", "S", False)]


def test_post_llm_call_resolves_prior_then_stages_current() -> None:
    eng = _FakeEngine()
    cb = hooks.make_post_llm_call(eng)
    cb(session_id="S", user_message="no, that's wrong", assistant_response="...", turn_id="t2")
    # Order matters: resolve the prior turn (with this feedback) BEFORE staging the new one.
    kinds = [c[0] for c in eng.calls]
    assert kinds == ["resolve_pending", "stage_turn"]
    assert eng.calls[0] == ("resolve_pending", "S", "no, that's wrong")
    assert eng.calls[1] == ("stage_turn", "S", "t2")


def test_on_session_end_flushes() -> None:
    eng = _FakeEngine()
    cb = hooks.make_on_session_end(eng)
    cb(session_id="S", completed=True)
    assert eng.calls == [("flush_pending", "S")]


def test_hooks_never_raise_on_missing_session() -> None:
    eng = _FakeEngine()
    # No session_id → callbacks must fail-soft (hooks are fail-open).
    hooks.make_post_tool_call(eng)(status="success")
    hooks.make_post_llm_call(eng)(user_message="x")
    hooks.make_on_session_end(eng)()
    # Nothing recorded for empty session, but no exception raised.
    assert all(c[1] == "" for c in eng.calls)
