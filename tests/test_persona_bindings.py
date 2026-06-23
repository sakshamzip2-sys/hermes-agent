"""Unit tests for gateway.persona_bindings — per-chat specialized-agent bindings.

Covers slug validation, the catalog, per-chat binding persistence (round-trip +
across a simulated reload), thread-scoped keys, and operator config defaults.
The binding store is redirected to a tmp dir via HERMES_HOME so tests never
touch the real ~/.hermes.
"""

import json
from dataclasses import dataclass
from typing import Optional

import pytest

import gateway.persona_bindings as pb


@dataclass
class _FakePlatform:
    value: str


@dataclass
class _FakeSource:
    platform: _FakePlatform
    chat_id: str
    thread_id: Optional[str] = None


def _src(platform="telegram", chat_id="100", thread_id=None):
    return _FakeSource(_FakePlatform(platform), chat_id, thread_id)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Redirect the binding store + home-derived lookups to a tmp dir.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


# ---------------------------------------------------------------------------
# slug validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("slug,ok", [
    ("finance", True),
    ("deep-research", True),
    ("knowledge-work", True),
    ("a", True),
    ("FINANCE", True),        # normalized to lowercase, like api_server's oc_agent_id
    ("Bad Slug", False),
    ("../etc", False),
    ("a/b", False),
    ("-leading", False),
    ("", False),
    ("x" * 65, False),
    (None, False),
    (123, False),
])
def test_is_valid_slug(slug, ok):
    assert pb.is_valid_slug(slug) is ok


def test_normalize_slug_lowercases_and_trims():
    assert pb.normalize_slug("  Finance ") == "finance"
    assert pb.normalize_slug("../bad") is None
    assert pb.normalize_slug(None) is None


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def test_catalog_contains_gallery_agents_and_excludes_subagents():
    known = pb.list_known_agents()
    # Shipped gallery agents (profile_templates dirs).
    for slug in ("finance", "legal", "deep-research", "knowledge-work"):
        assert slug in known, f"{slug} should be in the channel agent menu"
    # Compound-engineering delegation sub-agents must NOT clutter the menu.
    assert not any(s.startswith("ce-") for s in known)
    assert known == sorted(known)


def test_agent_exists_true_for_gallery_false_for_bogus():
    assert pb.agent_exists("finance") is True
    assert pb.agent_exists("bogus-does-not-exist-xyz") is False
    assert pb.agent_exists("../traversal") is False
    assert pb.agent_exists(None) is False


# ---------------------------------------------------------------------------
# binding key
# ---------------------------------------------------------------------------

def test_binding_key_includes_platform_and_chat():
    assert pb.binding_key(_src("telegram", "555")) == "telegram:555"


def test_binding_key_thread_scoped():
    assert pb.binding_key(_src("discord", "guild#chan", "topic7")) == "discord:guild#chan:ttopic7"


def test_binding_key_none_when_incomplete():
    assert pb.binding_key(None) is None
    assert pb.binding_key(_FakeSource(_FakePlatform("telegram"), "")) is None


# ---------------------------------------------------------------------------
# binding persistence
# ---------------------------------------------------------------------------

def test_set_get_clear_roundtrip():
    s = _src("telegram", "abc")
    assert pb.get_bound_slug(s) is None
    assert pb.set_bound_slug(s, "finance") is True
    assert pb.get_bound_slug(s) == "finance"
    prev = pb.clear_bound_slug(s)
    assert prev == "finance"
    assert pb.get_bound_slug(s) is None


def test_set_normalizes_slug():
    s = _src("slack", "T1/C1")
    assert pb.set_bound_slug(s, "  Deep-Research ") is True
    assert pb.get_bound_slug(s) == "deep-research"


def test_set_rejects_invalid_slug():
    s = _src("telegram", "z")
    assert pb.set_bound_slug(s, "../evil") is False
    assert pb.get_bound_slug(s) is None


def test_bindings_are_independent_per_chat_and_thread():
    a = _src("telegram", "1")
    b = _src("telegram", "2")
    c = _src("telegram", "1", "topic")  # same chat, different thread
    pb.set_bound_slug(a, "finance")
    pb.set_bound_slug(b, "legal")
    pb.set_bound_slug(c, "deep-research")
    assert pb.get_bound_slug(a) == "finance"
    assert pb.get_bound_slug(b) == "legal"
    assert pb.get_bound_slug(c) == "deep-research"


def test_binding_persists_across_reload(_isolated_home):
    s = _src("whatsapp", "+15551234")
    pb.set_bound_slug(s, "knowledge-work")
    # Simulate a fresh process: the file on disk is the only state.
    store = _isolated_home / "persona_bindings.json"
    assert store.is_file()
    data = json.loads(store.read_text())
    assert data["bindings"]["whatsapp:+15551234"] == "knowledge-work"
    # A re-read returns the same binding.
    assert pb.get_bound_slug(s) == "knowledge-work"


def test_clear_missing_returns_none():
    assert pb.clear_bound_slug(_src("telegram", "never-bound")) is None


def test_corrupt_store_is_treated_as_empty(_isolated_home):
    (_isolated_home / "persona_bindings.json").write_text("{ not json")
    # Must not raise; behaves as no bindings.
    assert pb.get_bound_slug(_src("telegram", "x")) is None
    # And a subsequent set still works (overwrites the corrupt file atomically).
    assert pb.set_bound_slug(_src("telegram", "x"), "finance") is True
    assert pb.get_bound_slug(_src("telegram", "x")) == "finance"


# ---------------------------------------------------------------------------
# operator config defaults
# ---------------------------------------------------------------------------

def test_default_per_platform_override():
    cfg = {"personas": {"default": "finance", "defaults": {"telegram": "deep-research"}}}
    assert pb.default_slug_for_platform(_FakePlatform("telegram"), cfg) == "deep-research"
    assert pb.default_slug_for_platform(_FakePlatform("slack"), cfg) == "finance"


def test_default_none_when_unset_or_invalid():
    assert pb.default_slug_for_platform(_FakePlatform("telegram"), {}) is None
    assert pb.default_slug_for_platform(_FakePlatform("telegram"), {"personas": {}}) is None
    # Unknown slug in config does not resolve.
    assert pb.default_slug_for_platform(
        _FakePlatform("telegram"), {"personas": {"default": "no-such-agent"}}
    ) is None
