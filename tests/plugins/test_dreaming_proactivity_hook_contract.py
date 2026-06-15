"""Hook-contract tests — exercise the REAL plugin hook entrypoints with the exact
kwargs v2's core passes, closing the gap between unit tests (injected fakes) and the
wired host path.

Host call sites verified against source:
- proactivity ``pre_llm_call``: ``agent/turn_context.py`` passes session_id, task_id,
  turn_id, user_message, conversation_history, is_first_turn, model, platform,
  sender_id (+ telemetry_schema_version added by invoke_hook).
- dreaming ``on_session_start``/``on_session_end``: invoked with session/model kwargs.
"""

from __future__ import annotations

import time

import plugins.dreaming as dreaming
import plugins.proactivity as proactivity
from plugins.proactivity import config as pconfig
from plugins.proactivity import writeback
from plugins.proactivity.config import ProactivityConfig
from plugins.proactivity.models import EventState, Sensitivity, TrackedEvent

# The full kwarg set the host passes to pre_llm_call (plus an unexpected future one
# to prove the hook's **kwargs absorbs additions without breaking).
_HOST_PRE_LLM_KWARGS = dict(
    session_id="s1",
    task_id="t1",
    turn_id="1",
    user_message="hey",
    conversation_history=[],
    is_first_turn=True,
    model="some-model",
    platform="",
    sender_id="u1",
    telemetry_schema_version=1,
    a_future_kwarg="ignored",
)


def test_proactivity_pre_llm_call_surfaces_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(proactivity, "_home_dir", lambda: tmp_path / "proact")
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(writeback, "get_memory_dir", lambda: mem)
    monkeypatch.setattr(pconfig, "load_proactivity_config", lambda block=None: ProactivityConfig(enabled=True))

    now = time.time()
    store = proactivity._event_store()
    store.add_event(TrackedEvent(
        id="e1", title="the summit", starts_at=now - 7200, ends_at=now - 3600,
        source="user_told", sensitivity=Sensitivity.TOLD_FACT,
        state=EventState.TRACKED, created_at=now - 7200,
    ))

    kwargs = dict(_HOST_PRE_LLM_KWARGS)
    kwargs["user_message"] = "what's on my plate today?"
    out = proactivity._on_pre_llm_call(**kwargs)
    assert isinstance(out, dict) and "context" in out
    assert "the summit" in out["context"]
    # the underlying event was acked/surfaced via the real entrypoint (the moment
    # pipeline promotes the ended event and surfaces a check-in)
    assert store.get("e1").state is not EventState.TRACKED


def test_proactivity_pre_llm_call_returns_none_when_disabled(monkeypatch):
    monkeypatch.setattr(pconfig, "load_proactivity_config", lambda block=None: ProactivityConfig(enabled=False))
    assert proactivity._on_pre_llm_call(**_HOST_PRE_LLM_KWARGS) is None


def test_proactivity_pre_llm_call_never_raises_on_bad_state(monkeypatch):
    # Even if the store path is unwritable-ish, the hook must be fail-open (return None).
    monkeypatch.setattr(pconfig, "load_proactivity_config", lambda block=None: ProactivityConfig(enabled=True))
    monkeypatch.setattr(proactivity, "_engine", lambda config: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should swallow and return None, not raise.
    assert proactivity._on_pre_llm_call(**_HOST_PRE_LLM_KWARGS) is None


def test_dreaming_session_boundary_hook_triggers_runner(monkeypatch):
    from plugins.dreaming import runner

    calls = []
    monkeypatch.setattr(runner, "maybe_run_in_background", lambda **kw: calls.append(kw))
    # Host invokes on_session_end with session/model kwargs.
    dreaming._on_session_boundary(
        session_id="s1", task_id="t1", model="m", platform="", a_future_kwarg="ok"
    )
    assert len(calls) == 1


def test_dreaming_session_boundary_hook_fail_open(monkeypatch):
    from plugins.dreaming import runner

    def _boom(**kw):
        raise RuntimeError("background spawn failed")

    monkeypatch.setattr(runner, "maybe_run_in_background", _boom)
    # Must not propagate — hooks are fail-open.
    dreaming._on_session_boundary(session_id="s1")
