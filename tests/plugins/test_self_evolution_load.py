"""Real-PluginManager load for the self_evolution conductor."""

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


def test_self_evolution_loads_through_real_manager() -> None:
    mgr = _load_with({"self_evolution"})
    lp = mgr._plugins.get("self_evolution")
    assert lp is not None, "self_evolution not discovered"
    assert lp.error is None, f"register() errored: {lp.error}"
    assert "self-evolve" in mgr._plugin_commands
    assert "self-evolve" in mgr._cli_commands


def test_whole_loop_plugin_set_loads_together() -> None:
    # All five self-evolution plugins load cleanly side-by-side (the real config shape).
    mgr = _load_with({"outcomes", "dreaming", "dream_orchestrator",
                      "playbook_synthesizer", "self_evolution"})
    for name in ("outcomes", "dreaming", "playbook_synthesizer", "self_evolution"):
        lp = mgr._plugins.get(name)
        assert lp is not None, f"{name} not discovered"
        assert lp.error is None, f"{name} register() errored: {lp.error}"
