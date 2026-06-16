"""Tests for the http_request CORE tool (STEP 3).

All tests run against a local mock HTTP server bound to 127.0.0.1 — no live
network calls in CI. SSRF protection is exercised both pre-flight and on
redirect.
"""

import json
import socketserver
import threading
from http.server import BaseHTTPRequestHandler

import pytest

from tools.http_request_tool import http_request, _redact_headers
from tools import url_safety


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        if self.path.startswith("/json"):
            self._send(200, json.dumps({"ok": True, "path": self.path}))
        elif self.path == "/big":
            self._send(200, b"x" * 100_000, "text/plain")
        elif self.path == "/redir-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.end_headers()
        elif self.path == "/echo-auth":
            self._send(200, json.dumps({"auth": self.headers.get("Authorization")}))
        else:
            self._send(404, json.dumps({"err": "not found"}))

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        self._send(201, json.dumps({"got": data.decode(),
                                    "ctype": self.headers.get("Content-Type")}))


@pytest.fixture()
def mock_server(monkeypatch):
    # Allow 127.0.0.1 so the mock is reachable; reset the cached toggle.
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "true")
    url_safety._reset_allow_private_cache()
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _MockHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    url_safety._reset_allow_private_cache()


def _call(args):
    return json.loads(http_request(args))


def test_get_json(mock_server):
    r = _call({"method": "GET", "url": mock_server + "/json"})
    assert r["status"] == 200
    assert r["ok"] is True
    assert r["body_json"] == {"ok": True, "path": "/json"}
    assert isinstance(r["elapsed_ms"], (int, float))
    assert r["final_url"].endswith("/json")


def test_post_json_sets_content_type(mock_server):
    r = _call({"method": "POST", "url": mock_server + "/echo", "json": {"a": 1}})
    assert r["status"] == 201
    assert "application/json" in r["body_json"]["ctype"]
    assert json.loads(r["body_json"]["got"]) == {"a": 1}


def test_post_form_sets_content_type(mock_server):
    r = _call({"method": "POST", "url": mock_server + "/echo", "form": {"k": "v"}})
    assert "x-www-form-urlencoded" in r["body_json"]["ctype"]
    assert "k=v" in r["body_json"]["got"]


def test_post_text_body(mock_server):
    r = _call({"method": "POST", "url": mock_server + "/echo", "text": "hello"})
    assert r["body_json"]["got"] == "hello"
    assert "text/plain" in r["body_json"]["ctype"]


def test_query_encoding(mock_server):
    r = _call({"method": "GET", "url": mock_server + "/json",
               "query": {"q": "hello world", "n": 5}})
    assert "q=hello+world" in r["body_json"]["path"] or "q=hello%20world" in r["body_json"]["path"]
    assert "n=5" in r["body_json"]["path"]


def test_truncation(mock_server):
    r = _call({"method": "GET", "url": mock_server + "/big", "max_response_bytes": 1000})
    assert r["truncated"] is True
    assert len(r["body_text"]) == 1000


def test_non_2xx_is_surfaced_not_error(mock_server):
    r = _call({"method": "GET", "url": mock_server + "/missing"})
    assert r["status"] == 404
    assert r["ok"] is False
    assert "error" not in r  # a 404 is a result, not a tool error


def test_bearer_auth_sent(mock_server):
    r = _call({"method": "GET", "url": mock_server + "/echo-auth",
               "auth": {"bearer": "secret-token-123"}})
    assert r["body_json"]["auth"] == "Bearer secret-token-123"


def test_invalid_method():
    r = json.loads(http_request({"method": "TRACE", "url": "https://example.com"}))
    assert "error" in r
    assert "Unsupported method" in r["error"]


def test_two_body_types_rejected():
    r = json.loads(http_request({"method": "POST", "url": "https://example.com",
                                 "json": {"a": 1}, "text": "x"}))
    assert "error" in r


def test_missing_url():
    r = json.loads(http_request({"method": "GET"}))
    assert "error" in r


# --- SSRF protection (the security floor) ---

def test_ssrf_metadata_blocked_preflight(monkeypatch):
    # Without allow_private, a metadata target is blocked before any connection.
    monkeypatch.delenv("HERMES_ALLOW_PRIVATE_URLS", raising=False)
    url_safety._reset_allow_private_cache()
    r = json.loads(http_request({"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"}))
    assert "error" in r
    assert r.get("blocked") is True
    url_safety._reset_allow_private_cache()


def test_ssrf_redirect_to_metadata_blocked(mock_server):
    """A 302 to the metadata endpoint must be blocked mid-redirect even when
    127.0.0.1 is allowed for the initial request."""
    r = _call({"method": "GET", "url": mock_server + "/redir-metadata"})
    assert "error" in r
    assert "SSRF" in r["error"] or r.get("kind") == "ssrf"


# --- auth/header redaction in logs ---

def test_redact_headers_masks_sensitive():
    masked = _redact_headers({
        "Authorization": "Bearer abc", "Cookie": "session=xyz",
        "X-API-Key": "k", "Content-Type": "application/json",
    })
    assert masked["Authorization"] == "<redacted>"
    assert masked["Cookie"] == "<redacted>"
    assert masked["X-API-Key"] == "<redacted>"
    assert masked["Content-Type"] == "application/json"  # non-sensitive preserved


def test_timeout(monkeypatch):
    """A 1ms timeout against a slow/unroutable host returns a structured timeout."""
    monkeypatch.setenv("HERMES_ALLOW_PRIVATE_URLS", "true")
    url_safety._reset_allow_private_cache()
    # 10.255.255.1 is non-routable private space; with 1ms timeout it can't connect.
    r = json.loads(http_request({"method": "GET", "url": "http://10.255.255.1/",
                                 "timeout_ms": 1}))
    assert "error" in r
    assert r.get("kind") in ("timeout", "connect")
    url_safety._reset_allow_private_cache()
