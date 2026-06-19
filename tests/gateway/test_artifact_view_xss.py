"""Security regression: inline artifact-file view must not enable stored XSS.

A ``write_file``-produced artifact is attacker-influenceable (an agent or a
poisoned flow can write arbitrary files). The inline view endpoint
(GET /api/v1/sessions/{sid}/artifact-file?path=...) must therefore NEVER serve
text/html or image/svg+xml as renderable active content in the app origin —
that would be stored XSS. Only inert types render inline; everything else is
forced to opaque bytes + attachment, with nosniff and a locked-down CSP.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB

_SID = "sess-xss"


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    a = APIServerAdapter(PlatformConfig(enabled=True))
    a._session_db = session_db
    return a


def _app(adapter):
    app = web.Application()
    app.router.add_get(
        "/api/v1/sessions/{session_id}/artifact-file", adapter._handle_artifact_view
    )
    return app


async def _get(adapter, path):
    async with TestClient(TestServer(_app(adapter))) as cli:
        resp = await cli.get(
            f"/api/v1/sessions/{_SID}/artifact-file", params={"path": path}
        )
        return resp.status, dict(resp.headers)


def _ctype(headers):
    return headers["Content-Type"].split(";")[0].strip()


@pytest.mark.asyncio
async def test_html_artifact_not_served_as_renderable_html(adapter, tmp_path):
    f = tmp_path / "evil.html"
    f.write_text("<script>alert(document.domain)</script>")
    adapter._register_artifact(_SID, str(f))
    status, h = await _get(adapter, str(f))
    assert status == 200
    # The core fix: an .html artifact must NOT come back as text/html.
    assert _ctype(h) != "text/html"
    assert _ctype(h) == "application/octet-stream"
    assert h["Content-Disposition"].startswith("attachment")
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'none'" in h["Content-Security-Policy"]


@pytest.mark.asyncio
async def test_svg_artifact_not_served_as_svg(adapter, tmp_path):
    f = tmp_path / "evil.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
    adapter._register_artifact(_SID, str(f))
    status, h = await _get(adapter, str(f))
    assert status == 200
    assert _ctype(h) != "image/svg+xml"
    assert h["Content-Disposition"].startswith("attachment")


@pytest.mark.asyncio
async def test_png_artifact_renders_inline_safely(adapter, tmp_path):
    f = tmp_path / "ok.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    adapter._register_artifact(_SID, str(f))
    status, h = await _get(adapter, str(f))
    assert status == 200
    assert _ctype(h) == "image/png"
    assert h["Content-Disposition"].startswith("inline")
    assert h["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_unregistered_path_is_not_served(adapter, tmp_path):
    # Defense-in-depth already present: a path not among the session's artifacts
    # must 404 (no arbitrary filesystem read / traversal).
    secret = tmp_path / "secret.html"
    secret.write_text("<script>nope</script>")
    status, _ = await _get(adapter, str(secret))
    assert status == 404
