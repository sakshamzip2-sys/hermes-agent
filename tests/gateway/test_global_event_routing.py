"""End-to-end routing for global out-of-band events (monitors + channels).

Regression test for the W3 bug an adversarial review caught: monitor/channel
events flowed through completion_queue -> drain -> format but were SILENTLY
DROPPED at _inject_watch_notification because _build_process_event_source
returned None (no routing metadata).  The fix routes global events
(type=="channel" or session_key=="__monitors__") to the configured home channel,
and lets channels carry explicit routing.

These tests exercise the REAL GatewayRunner routing methods bound to a minimal
fake self, so they prove the full source-resolution path produces a deliverable
SessionSource (the link that was broken).
"""

from __future__ import annotations

import pytest

from gateway.config import HomeChannel, Platform
from gateway.run import GatewayRunner, _format_gateway_process_notification
from tools import channels


class _FakeStore:
    def _ensure_loaded(self):
        pass

    @property
    def _entries(self):
        return {}


class _FakeConfig:
    def __init__(self, home_by_platform):
        self._home = home_by_platform

    def get_home_channel(self, platform):
        return self._home.get(platform)


class _FakeGateway:
    """Minimal stand-in that borrows the real routing methods under test."""

    # Bind the real methods so we exercise production code, not a copy.
    _home_channel_source = GatewayRunner._home_channel_source
    _build_process_event_source = GatewayRunner._build_process_event_source

    def __init__(self, *, home=None, adapters=None):
        self.adapters = adapters or {Platform.TELEGRAM: object()}
        self.config = _FakeConfig(home or {})
        self.session_store = _FakeStore()

    def _get_cached_session_source(self, key):
        return None


def _home():
    return {
        Platform.TELEGRAM: HomeChannel(
            platform=Platform.TELEGRAM, chat_id="555", name="home"
        )
    }


# ── home-channel fallback for global events ───────────────────────────────────


def test_channel_event_routes_to_home_channel():
    gw = _FakeGateway(home=_home())
    evt = {"type": "channel", "source": "ci", "content": "deploy done"}
    src = gw._build_process_event_source(evt)
    assert src is not None, "channel event must resolve a SessionSource (was dropped)"
    assert src.platform == Platform.TELEGRAM
    assert src.chat_id == "555"


def test_monitor_event_routes_to_home_channel():
    gw = _FakeGateway(home=_home())
    evt = {"type": "watch_match", "session_key": "__monitors__", "pattern": "ERROR", "output": "boom"}
    src = gw._build_process_event_source(evt)
    assert src is not None, "monitor watch event must resolve a SessionSource (was dropped)"
    assert src.chat_id == "555"


def test_channel_explicit_routing_takes_precedence():
    # Explicit platform/chat_id on the event wins over the home channel.
    gw = _FakeGateway(home=_home())
    evt = {
        "type": "channel",
        "source": "ci",
        "content": "x",
        "platform": "telegram",
        "chat_id": "999",
        "chat_type": "group",
    }
    src = gw._build_process_event_source(evt)
    assert src is not None
    assert src.chat_id == "999"
    assert src.chat_type == "group"


def test_no_home_channel_still_returns_none():
    # With no home channel configured, a global event has nowhere to go.
    gw = _FakeGateway(home={})
    evt = {"type": "channel", "source": "ci", "content": "x"}
    assert gw._build_process_event_source(evt) is None


def test_non_global_event_unaffected_by_fallback():
    # A normal event with no routing and not global must still return None
    # (the home-channel fallback must NOT swallow ordinary process events).
    gw = _FakeGateway(home=_home())
    evt = {"type": "completion", "session_id": "p1"}
    assert gw._build_process_event_source(evt) is None


# ── inject_channel_event carries explicit routing into the evt ────────────────


def test_inject_channel_event_explicit_routing_round_trips():
    from tools.process_registry import process_registry

    # drain
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()

    channels.inject_channel_event(
        "app", "hi", platform="telegram", chat_id="42", chat_type="dm"
    )
    evt = process_registry.completion_queue.get_nowait()
    assert evt["platform"] == "telegram"
    assert evt["chat_id"] == "42"
    assert evt["chat_type"] == "dm"

    # And that event resolves to the explicit target via the real router.
    gw = _FakeGateway(home={})  # no home channel — must still route via explicit
    src = gw._build_process_event_source(evt)
    assert src is not None
    assert src.chat_id == "42"


def test_full_chain_format_then_route():
    """Drain -> format (<channel> tag) -> resolve source: the whole delivery path."""
    from tools.process_registry import process_registry

    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()

    channels.inject_channel_event("webhook", "build failed", meta={"severity": "high"})
    evt = process_registry.completion_queue.get_nowait()

    rendered = _format_gateway_process_notification(evt)
    assert rendered and "<channel" in rendered and "build failed" in rendered

    gw = _FakeGateway(home=_home())
    src = gw._build_process_event_source(evt)
    assert src is not None and src.chat_id == "555"
