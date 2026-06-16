"""Tests for the channels primitive (tools/channels.py).

Channels port the Claude Code "channels" concept: push an out-of-band event into
the live agent loop, delivered as ``<channel source=... attrs>body</channel>``.
The transport reuses the process-registry completion_queue that monitors/watch
already use; the gateway drains it after each turn (covered in gateway tests).
Here we pin the inject + format + sanitization contract.
"""

from __future__ import annotations

from tools import channels


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_inject_enqueues_channel_event():
    from tools.process_registry import process_registry

    _drain(process_registry.completion_queue)  # start clean
    ok = channels.inject_channel_event(
        "webhook", "build failed on main", meta={"severity": "high", "run_id": "1234"}
    )
    assert ok is True
    events = _drain(process_registry.completion_queue)
    assert len(events) == 1
    evt = events[0]
    assert evt["type"] == "channel"
    assert evt["source"] == "webhook"
    assert evt["content"] == "build failed on main"
    assert evt["meta"] == {"severity": "high", "run_id": "1234"}


def test_inject_empty_content_is_noop():
    from tools.process_registry import process_registry

    _drain(process_registry.completion_queue)
    assert channels.inject_channel_event("webhook", "   ") is False
    assert _drain(process_registry.completion_queue) == []


def test_format_channel_event_basic():
    evt = {
        "type": "channel",
        "source": "telegram",
        "content": "hello from phone",
        "meta": {"chat_id": "42"},
    }
    out = channels.format_channel_event(evt)
    assert out.startswith('<channel source="telegram" chat_id="42">')
    assert "hello from phone" in out
    assert out.rstrip().endswith("</channel>")


def test_meta_key_sanitization_drops_bad_keys():
    # Hyphenated / non-identifier keys are dropped; identifier keys survive.
    out = channels._safe_meta({"good_key": "v", "bad-key": "x", "9bad": "y", "ok2": "z"})
    assert out == {"good_key": "v", "ok2": "z"}


def test_meta_value_quote_and_newline_escaped():
    out = channels._safe_meta({"k": 'has"quote\nand newline'})
    assert '"' not in out["k"]
    assert "\n" not in out["k"]


def test_format_escapes_in_attributes():
    evt = {
        "source": "src",
        "content": "body",
        "meta": {"note": 'a"b'},
    }
    out = channels.format_channel_event(evt)
    # The injected attribute value must not contain a raw double-quote that would
    # break out of the attribute.
    assert 'note="a\'b"' in out


def test_gateway_drain_collects_channel_events():
    """The gateway post-turn drain must collect 'channel' events (not drop them)."""
    from tools.process_registry import process_registry
    from gateway.run import _drain_gateway_watch_events, _format_gateway_process_notification

    _drain(process_registry.completion_queue)
    channels.inject_channel_event("ci", "deploy done", meta={"env": "prod"})
    collected = _drain_gateway_watch_events(process_registry.completion_queue)
    assert any(e.get("type") == "channel" for e in collected)
    # And the gateway formatter renders it as a <channel> block.
    rendered = [_format_gateway_process_notification(e) for e in collected]
    assert any(r and "<channel" in r and "deploy done" in r for r in rendered)


def test_no_vendor_name_in_channels():
    import os

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    path = os.path.join(repo_root, "tools", "channels.py")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read().lower()
    for vendor in ("import anthropic", "import openai", "claude-", "gpt-4", "gemini-", "opus", "sonnet"):
        assert vendor not in src
