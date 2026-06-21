"""HTTP-level test for the parallel-agents SSE endpoint (real aiohttp client).

Verifies the wired endpoint over a real aiohttp TestServer (no live gateway, no
restart): a fresh client gets a snapshot frame; a reconnecting client with
Last-Event-ID gets only the deltas since its cursor, replayed from the durable
spine. Uses asyncio.run (no pytest-asyncio dependency).
"""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from plugins.oc_runs import db as spine_db
from plugins.oc_runs import events as ev
from plugins.oc_runs import sse_endpoint


def _reset():
    for attr in ("conn", "path"):
        if hasattr(spine_db._local, attr):
            try:
                if attr == "conn" and spine_db._local.conn is not None:
                    spine_db._local.conn.close()
            except Exception:
                pass
            delattr(spine_db._local, attr)


@pytest.fixture()
def spine(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_OC_RUNS_DB", str(tmp_path / "oc_runs.db"))
    _reset()
    yield
    _reset()


def _make_app():
    app = web.Application()
    app.router.add_get("/ev", sse_endpoint.stream_events)
    return app


def test_fresh_client_gets_snapshot(spine):
    spine_db.append_event(ev.build_event("agents:a", ev.RUN_CREATED, source=ev.SOURCE_AGENTS,
                                         payload={"name": "demo"}))

    async def run():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/ev", params={"once": "1"})
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            body = await resp.text()
            assert "event: snapshot" in body
            assert "agents:a" in body

    asyncio.run(run())


def test_reconnect_with_last_event_id_gets_only_deltas(spine):
    s1 = spine_db.append_event(ev.build_event("agents:a", ev.RUN_CREATED, source=ev.SOURCE_AGENTS))
    s2 = spine_db.append_event(ev.build_event("agents:a", ev.RUN_STATUS, source=ev.SOURCE_AGENTS,
                                              payload={"status": "running"}))
    s3 = spine_db.append_event(ev.build_event("agents:a", ev.RUN_COMPLETED, source=ev.SOURCE_AGENTS))

    async def run():
        async with TestClient(TestServer(_make_app())) as client:
            resp = await client.get("/ev", params={"once": "1"},
                                    headers={"Last-Event-ID": str(s1)})
            body = await resp.text()
            # Resume: deltas since s1 only, no fresh snapshot.
            assert "event: snapshot" not in body
            assert f"id: {s2}" in body
            assert f"id: {s3}" in body
            assert f"id: {s1}" not in body

    asyncio.run(run())
