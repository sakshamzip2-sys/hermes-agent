"""Tests for the ENRICH↑ feed (local outcomes → GBrain). Injected rpc_fn, no network."""

from __future__ import annotations

from plugins.self_evolution.feed_up import build_signal_page, feed_up


def test_signal_page_renders_frontmatter_and_bands() -> None:
    slug, md = build_signal_page([("sess-A", 0.9), ("sess-B", 0.4), ("sess-C", 0.5)])
    assert slug == "agent-self-evolution-signal"
    assert md.startswith("---\n")
    assert "sess-A" in md and "0.900" in md and "(good)" in md
    assert "(poor)" in md   # 0.4
    assert "(mixed)" in md  # 0.5


def test_empty_scores_is_noop() -> None:
    out = feed_up([], rpc_fn=lambda *a, **k: {}, token="x")
    assert out["ok"] is True
    assert out["skipped"] == "no scored sessions"


def test_feed_up_calls_put_page() -> None:
    captured = {}

    def rpc_fn(method, params, *, token, timeout):
        captured["method"] = method
        captured["tool"] = params["name"]
        captured["slug"] = params["arguments"]["slug"]
        captured["content"] = params["arguments"]["content"]
        return {"result": {"ok": True}}

    out = feed_up([("s1", 0.8), ("s2", 0.3)], rpc_fn=rpc_fn, token="tok")
    assert out["ok"] is True and out["fed"] == 2
    assert captured["method"] == "tools/call"
    assert captured["tool"] == "put_page"
    assert captured["slug"] == "agent-self-evolution-signal"
    assert "s1" in captured["content"]


def test_feed_up_is_fail_soft_on_rpc_error() -> None:
    def rpc_fn(method, params, *, token, timeout):
        raise RuntimeError("gbrain down")

    out = feed_up([("s1", 0.8)], rpc_fn=rpc_fn, token="tok")
    assert out["ok"] is False
    assert "gbrain down" in out["error"]


def test_feed_up_reports_rpc_error_payload() -> None:
    out = feed_up([("s1", 0.8)], rpc_fn=lambda *a, **k: {"error": {"code": -1, "message": "bad"}}, token="t")
    assert out["ok"] is False


def test_no_token_skips() -> None:
    out = feed_up([("s1", 0.8)], rpc_fn=lambda *a, **k: {}, token="")
    assert out["ok"] is True
    assert "GBRAIN_MCP_TOKEN" in out["skipped"]
