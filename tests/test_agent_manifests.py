"""Verify the shipped agent manifests parse into real AgentDefinitions.

Guards against a placeholder manifest: the coder (forge) and reviewer must load
with the exact capability fields we authored (toolsets, model, permission mode,
turn budget) and the display-only extras the gallery uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tools.agent_defs import (
    get_agent_definition,
    load_agent_definitions,
    resolve_agent_overrides,
    to_manifest_dict,
)

AGENTS_DIR = Path(__file__).resolve().parents[1] / ".hermes" / "agents"


def test_forge_is_a_real_coder_definition():
    d = get_agent_definition("forge", dirs=[AGENTS_DIR])
    assert d is not None
    # Real capability grants (valid registered toolset names), not a prompt-only
    # persona. 'file' covers read/write/patch/grep; 'code_execution' runs code.
    for ts in ("file", "code_execution", "terminal", "lsp"):
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
    assert d.toolsets == ["file"]  # read-only (file covers read + search_files/grep)
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
    assert "code_execution" in (get_agent_definition("ledger", dirs=[AGENTS_DIR]).toolsets or [])


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


# --------------------------------------------------------------------------- #
# the _create_agent resolve seam (pure helper)
# --------------------------------------------------------------------------- #

def test_resolve_overrides_for_forge():
    ov = resolve_agent_overrides("forge", dirs=[AGENTS_DIR])
    assert ov is not None
    assert "file" in ov["toolsets"] and "code_execution" in ov["toolsets"]
    assert ov["model"] == "claude-sonnet-4-6"
    assert ov["permission_mode"] == "plan"
    assert ov["max_iterations"] == 12
    assert ov["status"] == "active"


def test_resolve_overrides_none_for_missing_agent():
    assert resolve_agent_overrides("does-not-exist", dirs=[AGENTS_DIR]) is None


def test_resolve_overrides_surfaces_archived_status():
    ov = resolve_agent_overrides("scout", dirs=[AGENTS_DIR])
    assert ov is not None
    assert ov["status"] == "archived"


def test_to_manifest_dict_for_atlas():
    d = get_agent_definition("atlas", dirs=[AGENTS_DIR])
    m = to_manifest_dict(d)
    assert m["display_name"] == "Atlas"
    assert m["featured"] is True
    assert m["status"] == "active"
    assert isinstance(m["starters"], list) and len(m["starters"]) >= 1


# --------------------------------------------------------------------------- #
# add_agent.sh scaffolder (the one-step add path)
# --------------------------------------------------------------------------- #

def test_add_agent_scaffolds_a_parseable_manifest(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts" / "add_agent.sh"
    env = dict(os.environ)
    env["HERMES_AGENTS_DIR"] = str(tmp_path)

    r = subprocess.run(["bash", str(script), "sales"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "sales.md").exists()
    d = get_agent_definition("sales", dirs=[tmp_path])
    assert d is not None
    assert d.toolsets == ["file", "web", "memory"]

    # Refuses to overwrite an existing manifest.
    r2 = subprocess.run(["bash", str(script), "sales"], env=env, capture_output=True, text=True)
    assert r2.returncode != 0

    # Rejects a path-traversal / unsafe slug.
    r3 = subprocess.run(["bash", str(script), "../evil"], env=env, capture_output=True, text=True)
    assert r3.returncode != 0


# --------------------------------------------------------------------------- #
# Regression guard: every declared toolset must be a REAL registered toolset.
# This catches the class of bug where a manifest grants a non-existent toolset
# name (e.g. "files"/"code"/"search"), which is silently ignored at runtime so
# the agent never actually gets the capability.
# --------------------------------------------------------------------------- #

import re  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _registered_toolset_names() -> set:
    """Extract real toolset names from the tools/ source (toolset="..."
    declarations). Self-updating: stays correct as tools are added/removed."""
    names = set()
    for py in (REPO_ROOT / "tools").rglob("*.py"):
        try:
            txt = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in re.finditer(r"""toolset\s*=\s*["']([a-z_]+)["']""", txt):
            names.add(m.group(1))
    return names


def test_every_shipped_manifest_toolset_is_real():
    valid = _registered_toolset_names()
    # Sanity: the extraction found the core toolsets.
    assert {"file", "terminal", "code_execution", "memory"} <= valid, valid
    defs = load_agent_definitions(dirs=[AGENTS_DIR])
    bad = {}
    for name, d in defs.items():
        for ts in (d.toolsets or []):
            if ts not in valid:
                bad.setdefault(name, []).append(ts)
    assert not bad, f"manifests declare unregistered toolsets: {bad}"
