"""Tests for plugin-declared background monitors (hermes_cli.monitor_manager).

Verifies the declarative + lifecycle layer the Claude Code "monitors" concept
adds on top of the existing process-registry watch runtime: manifest discovery,
``when`` filtering, idempotent start, watch-pattern wiring, list, and stop.  The
watch_match -> completion_queue -> agent path is pre-existing and tested in the
process_registry suite; here we prove monitors are correctly spawned + tracked.
"""

from __future__ import annotations

import pytest

from hermes_cli import monitor_manager as mm
from hermes_cli.plugins import LoadedPlugin, PluginManifest


class _FakeManager:
    """Minimal stand-in exposing the ``_plugins`` dict monitor_manager reads."""

    def __init__(self, loaded):
        self._plugins = {lp.manifest.key or lp.manifest.name: lp for lp in loaded}


def _loaded(key, monitors, enabled=True):
    return LoadedPlugin(
        manifest=PluginManifest(name=key, key=key, monitors=monitors),
        enabled=enabled,
    )


@pytest.fixture(autouse=True)
def _clean():
    mm.reset_for_test()
    yield
    # Never leave background monitor processes running after a test.
    mm.stop_all_monitors()
    mm.reset_for_test()


def test_starts_always_monitor_and_sets_watch_patterns():
    mgr = _FakeManager([
        _loaded("p1", [{
            "name": "poller",
            "command": "sleep 30",
            "watch_patterns": ["READY", "FAILED"],
            "when": "always",
        }]),
    ])
    started = mm.start_plugin_monitors(plugin_manager=mgr)
    assert len(started) == 1
    rec = started[0]
    assert rec["key"] == "p1:poller"
    assert rec["watch_patterns"] == ["READY", "FAILED"]
    assert rec["session_id"]
    # Tracked + listable
    listing = mm.list_monitors()
    assert any(m["key"] == "p1:poller" for m in listing)


def test_idempotent_no_duplicate_start():
    mgr = _FakeManager([
        _loaded("p1", [{"name": "m", "command": "sleep 30", "when": "always"}]),
    ])
    first = mm.start_plugin_monitors(plugin_manager=mgr)
    second = mm.start_plugin_monitors(plugin_manager=mgr)
    assert len(first) == 1
    assert second == []  # already running -> not restarted
    assert len(mm.list_monitors()) == 1


def test_disabled_plugin_monitors_not_started():
    mgr = _FakeManager([
        _loaded("p1", [{"name": "m", "command": "sleep 30", "when": "always"}], enabled=False),
    ])
    assert mm.start_plugin_monitors(plugin_manager=mgr) == []


def test_when_filter_skips_non_matching():
    mgr = _FakeManager([
        _loaded("p1", [
            {"name": "always_one", "command": "sleep 30", "when": "always"},
            {"name": "skill_one", "command": "sleep 30", "when": "on-skill-invoke:deploy"},
        ]),
    ])
    started = mm.start_plugin_monitors(plugin_manager=mgr)
    keys = {s["key"] for s in started}
    assert keys == {"p1:always_one"}  # skill-gated one not started at startup


def test_start_monitors_for_skill():
    mgr = _FakeManager([
        _loaded("p1", [
            {"name": "skill_one", "command": "sleep 30", "when": "on-skill-invoke:deploy"},
        ]),
    ])
    # Not started at startup
    assert mm.start_plugin_monitors(plugin_manager=mgr) == []
    # Started when the skill fires
    started = mm.start_monitors_for_skill("deploy", plugin_manager=mgr)
    assert {s["key"] for s in started} == {"p1:skill_one"}


def test_empty_command_skipped():
    mgr = _FakeManager([
        _loaded("p1", [{"name": "bad", "command": "   ", "when": "always"}]),
    ])
    assert mm.start_plugin_monitors(plugin_manager=mgr) == []


def test_stop_all_clears_tracking():
    mgr = _FakeManager([
        _loaded("p1", [{"name": "m", "command": "sleep 30", "when": "always"}]),
    ])
    mm.start_plugin_monitors(plugin_manager=mgr)
    assert len(mm.list_monitors()) == 1
    stopped = mm.stop_all_monitors()
    assert stopped >= 1
    assert mm.list_monitors() == []


def test_manifest_parses_monitors_field(tmp_path):
    """plugin.yaml monitors: field is parsed into PluginManifest.monitors."""
    import yaml as _yaml
    from hermes_cli.plugins import PluginManager

    plug = tmp_path / "myplugin"
    plug.mkdir()
    (plug / "plugin.yaml").write_text(
        _yaml.safe_dump({
            "name": "myplugin",
            "monitors": [
                {"name": "log", "command": "tail -F x.log", "watch_patterns": ["ERROR"]},
                {"name": "bad", "note": "no command -> dropped"},
            ],
        }),
        encoding="utf-8",
    )
    pmgr = PluginManager()
    manifest = pmgr._parse_manifest(plug / "plugin.yaml", plug, source="user", prefix="")
    assert manifest is not None
    # Only the entry with a command survives.
    assert len(manifest.monitors) == 1
    assert manifest.monitors[0]["name"] == "log"
    assert manifest.monitors[0]["watch_patterns"] == ["ERROR"]


def test_no_vendor_name_in_monitor_manager():
    import os

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    path = os.path.join(repo_root, "hermes_cli", "monitor_manager.py")
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read().lower()
    for vendor in ("import anthropic", "import openai", "claude-", "gpt-4", "gemini-", "opus", "sonnet"):
        assert vendor not in src
