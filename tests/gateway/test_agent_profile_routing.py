"""Tests for per-agent profile DB routing in the API server.

Each frontend "agent" maps to its own backend profile = its own
``{hermes_home}/agent-profiles/{slug}/state.db`` (its own sessions, message
history and FTS5 search), fully isolated from the shared main-agent db and from
every other agent. Session reads and deletes are routed to the correct profile
db via the ``X-OpenComputer-Agent-Id`` header so that:

  * resuming an agent chat returns the messages its turns persisted, and
  * deleting an agent chat purges it from BOTH the shared db (where its
    metadata row lives for the global session list) and the agent's profile db
    (where its turns live) — leaving no orphaned messages behind.
"""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_db(tmp_path):
    """The shared main-agent state.db (what _ensure_session_db returns)."""
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Point get_hermes_home() at the temp dir so agent-profiles land there.

    _get_agent_profile_db imports get_hermes_home from hermes_constants at call
    time, so patching the module attribute is deterministic regardless of the
    aiohttp handler's task context (a ContextVar override would be racy here).
    """
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def adapter(session_db):
    a = APIServerAdapter(PlatformConfig(enabled=True))
    a._session_db = session_db
    try:
        yield a
    finally:
        for db in a._agent_profile_dbs.values():
            close = getattr(db, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass


def _create_session_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get("/api/sessions/{session_id}", adapter._handle_get_session)
    app.router.add_delete("/api/sessions/{session_id}", adapter._handle_delete_session)
    app.router.add_get(
        "/api/sessions/{session_id}/messages", adapter._handle_session_messages
    )
    return app


# ---------------------------------------------------------------------------
# _parse_oc_agent_id — pure slug validation (path-traversal / injection guard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("atlas", "atlas"),
        ("Atlas", "atlas"),  # lowercased
        ("forge-2", "forge-2"),
        ("a1", "a1"),
        ("  scout  ", "scout"),  # stripped
        ("x" * 64, "x" * 64),  # exactly the 64-char limit is allowed
    ],
)
def test_parse_oc_agent_id_accepts_valid_slugs(raw, expected):
    assert APIServerAdapter._parse_oc_agent_id({"oc_agent_id": raw}) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "../etc/passwd",
        "atlas/../forge",
        "a/b",
        "a.b",
        "a_b",  # underscore not in the allowed charset
        "-leading",  # must start with [a-z0-9]
        "UPPER CASE",
        "x" * 65,  # over the 64-char limit
        None,
        123,
        {"nested": "obj"},
    ],
)
def test_parse_oc_agent_id_rejects_unsafe(raw):
    assert APIServerAdapter._parse_oc_agent_id({"oc_agent_id": raw}) is None


def test_parse_oc_agent_id_missing_key_is_none():
    assert APIServerAdapter._parse_oc_agent_id({}) is None


# ---------------------------------------------------------------------------
# _get_agent_profile_db — isolation, caching, on-disk layout
# ---------------------------------------------------------------------------


def test_profile_dbs_are_isolated_and_cached(adapter, hermes_home):
    atlas = adapter._get_agent_profile_db("atlas")
    forge = adapter._get_agent_profile_db("forge")

    assert atlas is not None and forge is not None
    assert atlas is not forge
    # Caching: a repeat call returns the very same connection object.
    assert adapter._get_agent_profile_db("atlas") is atlas
    # The default/main agent (no slug) uses the shared db → None here.
    assert adapter._get_agent_profile_db(None) is None
    assert adapter._get_agent_profile_db("") is None

    # Data written to one profile is invisible to the other and to the shared db.
    atlas.create_session("s1", "api_server")
    assert atlas.get_session("s1") is not None
    assert forge.get_session("s1") is None
    assert adapter._session_db.get_session("s1") is None

    # On disk, each agent has its own state.db under agent-profiles/<slug>/.
    assert (hermes_home / "agent-profiles" / "atlas" / "state.db").exists()
    assert (hermes_home / "agent-profiles" / "forge" / "state.db").exists()


def test_profile_db_has_fts_search_tables(adapter, hermes_home):
    """Each agent profile gets full FTS5 search, just like the main agent."""
    atlas = adapter._get_agent_profile_db("atlas")
    atlas.create_session("s1", "api_server")
    atlas.append_message("s1", role="user", content="searchable haystack needle")

    # search_messages is the FTS5-backed search used across the product; it
    # returns a highlighted ``snippet`` (e.g. ">>>needle<<<") plus context.
    hits = atlas.search_messages("needle")
    assert hits, "FTS5 search returned no hits for an indexed message"
    assert any("needle" in (h.get("snippet") or "") for h in hits)


# ---------------------------------------------------------------------------
# Routing via X-OpenComputer-Agent-Id header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_routed_to_profile_db(adapter, hermes_home):
    """GET messages with the agent header reads the agent's profile db."""
    atlas = adapter._get_agent_profile_db("atlas")
    sid = "api_test_msgs"
    atlas.create_session(sid, "api_server")
    atlas.append_message(sid, role="user", content="profile-only message")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        # WITH header → reads the atlas profile db → finds the message.
        r1 = await cli.get(
            f"/api/sessions/{sid}/messages",
            headers={"X-OpenComputer-Agent-Id": "atlas"},
        )
        assert r1.status == 200
        d1 = await r1.json()
        assert any(m.get("content") == "profile-only message" for m in d1["data"])

        # WITHOUT header → shared db → session isn't there → 404 (isolation).
        r2 = await cli.get(f"/api/sessions/{sid}/messages")
        assert r2.status == 404


# ---------------------------------------------------------------------------
# Delete purges BOTH dbs (the split-brain cleanup fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_purges_shared_and_profile_dbs(adapter, hermes_home):
    shared = adapter._session_db
    atlas = adapter._get_agent_profile_db("atlas")
    sid = "api_test_splitbrain"

    # Split-brain: metadata row in the shared db, turns in the profile db.
    shared.create_session(sid, "api_server")
    atlas.create_session(sid, "api_server")
    atlas.append_message(sid, role="user", content="hello atlas")
    assert shared.get_session(sid) is not None
    assert atlas.get_session(sid) is not None
    assert len(atlas.get_messages(sid)) == 1

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.delete(
            f"/api/sessions/{sid}", headers={"X-OpenComputer-Agent-Id": "atlas"}
        )
        assert resp.status == 200
        assert (await resp.json())["deleted"] is True

    # Purged from BOTH — no orphaned rows left in the profile db.
    assert shared.get_session(sid) is None
    assert atlas.get_session(sid) is None
    assert atlas.get_messages(sid) == []


@pytest.mark.asyncio
async def test_delete_profile_only_session(adapter, hermes_home):
    """A session that only ever lived in the profile db is still deletable."""
    atlas = adapter._get_agent_profile_db("atlas")
    sid = "api_profile_only"
    atlas.create_session(sid, "api_server")
    atlas.append_message(sid, role="user", content="x")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.delete(
            f"/api/sessions/{sid}", headers={"X-OpenComputer-Agent-Id": "atlas"}
        )
        assert resp.status == 200
        assert (await resp.json())["deleted"] is True
    assert atlas.get_session(sid) is None


@pytest.mark.asyncio
async def test_delete_shared_only_session_without_header(adapter, hermes_home):
    """Regression: a normal (non-agent) session still deletes via the shared db."""
    shared = adapter._session_db
    sid = "api_shared_only"
    shared.create_session(sid, "api_server")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.delete(f"/api/sessions/{sid}")
        assert resp.status == 200
        assert (await resp.json())["deleted"] is True
    assert shared.get_session(sid) is None


@pytest.mark.asyncio
async def test_delete_missing_session_returns_404(adapter, hermes_home):
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.delete(
            "/api/sessions/does_not_exist",
            headers={"X-OpenComputer-Agent-Id": "atlas"},
        )
        assert resp.status == 404
