"""Verify the shipped agent manifests parse into real AgentDefinitions.

Guards against a placeholder manifest: the coder (forge) and reviewer must load
with the exact capability fields we authored (toolsets, model, permission mode,
turn budget) and the display-only extras the gallery uses.
"""

from __future__ import annotations

from pathlib import Path

from tools.agent_defs import get_agent_definition, load_agent_definitions

AGENTS_DIR = Path(__file__).resolve().parents[1] / ".hermes" / "agents"


def test_forge_is_a_real_coder_definition():
    d = get_agent_definition("forge", dirs=[AGENTS_DIR])
    assert d is not None
    # Real capability grants, not a prompt-only persona.
    for ts in ("files", "patch", "code", "search"):
        assert ts in (d.toolsets or []), f"forge missing toolset {ts}"
    assert d.model == "claude-sonnet-4-6"
    assert d.permission_mode == "plan"
    assert d.max_iterations == 12  # the verify-loop turn budget
    assert d.memory == "user"
    # Gallery display extras.
    assert d.extra.get("display_name") == "Forge"
    assert d.extra.get("featured") is True
    assert isinstance(d.extra.get("starters"), list) and len(d.extra["starters"]) >= 1
    # The body carries the gather/act/verify contract.
    assert "verify" in (d.prompt or "").lower()


def test_reviewer_is_read_only_with_structured_verdict():
    d = get_agent_definition("reviewer", dirs=[AGENTS_DIR])
    assert d is not None
    assert d.toolsets == ["files", "search"]  # read-only
    assert d.model == "claude-haiku-4-5"  # different family from forge (sonnet)
    assert d.permission_mode == "plan"
    body = (d.prompt or "").upper()
    assert "PASS" in body and "REVISE" in body and "REJECT" in body


def test_all_shipped_manifests_load_clean():
    defs = load_agent_definitions(dirs=[AGENTS_DIR])
    assert "forge" in defs
    assert "reviewer" in defs


def test_roster_keep_agents_parse_with_real_grants():
    for slug in ("atlas", "sage", "ledger"):
        d = get_agent_definition(slug, dirs=[AGENTS_DIR])
        assert d is not None, f"{slug} manifest missing"
        assert d.extra.get("status", "active") == "active"
        assert d.toolsets, f"{slug} has no toolsets"
    # Ledger is differentiated by a real compute grant, not just prompt text.
    assert "code" in get_agent_definition("ledger", dirs=[AGENTS_DIR]).toolsets


def test_quill_is_merged_reversibly():
    d = get_agent_definition("quill", dirs=[AGENTS_DIR])
    assert d is not None
    assert d.extra.get("status") == "merged"
    assert d.extra.get("merged_into") == "atlas"
    # Retained on disk (reversible), not deleted.
    assert (AGENTS_DIR / "quill.md").exists()


def test_scout_is_archived_reversibly():
    d = get_agent_definition("scout", dirs=[AGENTS_DIR])
    assert d is not None
    assert d.extra.get("status") == "archived"
    # Retained on disk (reversible by flipping the flag), not deleted.
    assert (AGENTS_DIR / "scout.md").exists()


def test_full_roster_loads_without_error():
    defs = load_agent_definitions(dirs=[AGENTS_DIR])
    for slug in ("atlas", "forge", "sage", "ledger", "quill", "scout", "reviewer"):
        assert slug in defs, f"{slug} failed to load"
