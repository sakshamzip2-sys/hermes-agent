"""GAP2: GET /api/agents/manifests endpoint (testable helper + route).

Verifies build_agent_manifests returns gallery-ready manifest metadata for the
shipped roster, hides archived by default but includes them on request, and that
the route is actually registered. Exercises the real shipped manifests.
"""

from __future__ import annotations

import inspect

from gateway.platforms.api_server import APIServerAdapter


def test_manifests_payload_shape_and_roster():
    out = APIServerAdapter.build_agent_manifests()
    assert out["object"] == "hermes.agent_manifests"
    assert out["error"] is None
    names = {m["name"] for m in out["manifests"]}
    # Active roster present; the forge coder is featured with real grants.
    assert {"forge", "atlas", "sage", "ledger"} <= names
    forge = next(m for m in out["manifests"] if m["name"] == "forge")
    assert forge["display_name"] == "Forge"
    assert forge["featured"] is True
    assert "code_execution" in (forge["toolsets"] or [])


def test_archived_hidden_by_default_shown_on_request():
    default = {m["name"] for m in APIServerAdapter.build_agent_manifests()["manifests"]}
    witharch = {m["name"] for m in
                APIServerAdapter.build_agent_manifests(include_archived=True)["manifests"]}
    # scout is archived -> hidden by default, present when requested.
    assert "scout" not in default
    assert "scout" in witharch


def test_route_is_registered():
    src = inspect.getsource(APIServerAdapter)
    assert 'add_get("/api/agents/manifests"' in src
    assert "_handle_agent_manifests" in src
