"""Real-loader integration test for the outcomes plugin.

Drives v2's ACTUAL PluginManager.discover_and_load so any manifest error, import
failure, or PluginContext-API misuse surfaces here. Mirrors
test_dreaming_proactivity_real_load.py.
"""

from __future__ import annotations

import hermes_cli.plugins as P


def _load_with(enabled: set) -> P.PluginManager:
    mgr = P.PluginManager()
    original = P._get_enabled_plugins
    P._get_enabled_plugins = lambda: enabled
    try:
        mgr.discover_and_load(force=True)
    finally:
        P._get_enabled_plugins = original
    return mgr


def _loaded(mgr: P.PluginManager, name: str):
    plugins = mgr._plugins
    items = plugins.items() if isinstance(plugins, dict) else [
        (getattr(lp.manifest, "name", None), lp) for lp in plugins
    ]
    for key, lp in items:
        nm = getattr(getattr(lp, "manifest", None), "name", key)
        if nm == name:
            return lp
    return None


def test_outcomes_loads_clean_through_real_manager():
    mgr = _load_with({"outcomes"})
    lp = _loaded(mgr, "outcomes")
    assert lp is not None, "outcomes plugin not discovered/loaded"
    assert lp.error is None, f"outcomes register() errored: {lp.error}"
    hooks = set(getattr(lp, "hooks_registered", []) or [])
    assert {"post_tool_call", "post_llm_call", "on_session_end"} <= hooks
    assert "outcomes" in mgr._plugin_commands   # /outcomes slash command
    assert "outcomes" in mgr._cli_commands       # hermes outcomes
    assert "outcomes" in mgr._aux_tasks          # auxiliary.outcomes judge task


def test_outcomes_is_opt_in():
    # Discovered but not activated when not enabled → register() must not run.
    mgr = _load_with(set())
    o = mgr._plugins.get("outcomes")
    if o is not None:
        assert (getattr(o, "hooks_registered", []) or []) == []
        assert o.error and "not enabled" in o.error.lower()
    assert "outcomes" not in mgr._plugin_commands
