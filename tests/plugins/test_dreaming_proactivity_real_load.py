"""Real-loader integration test.

Unlike the other tests (which import the plugin packages directly or use a fake
ctx), this drives v2's ACTUAL ``PluginManager.discover_and_load`` so that:
  - the plugins are discovered from the bundled ``plugins/`` directory,
  - their modules are imported in the real environment,
  - their ``register(ctx)`` runs through the genuine ``PluginContext``.

Any real manifest error, bad ``kind``, import failure, or PluginContext-API misuse
surfaces here rather than only in unit tests.
"""

from __future__ import annotations

import hermes_cli.plugins as P


def _load_with(enabled: set) -> P.PluginManager:
    mgr = P.PluginManager()
    # Force our standalone plugins to be enabled (they are opt-in via plugins.enabled).
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


def test_dreaming_loads_clean_through_real_manager():
    mgr = _load_with({"dreaming", "proactivity"})
    lp = _loaded(mgr, "dreaming")
    assert lp is not None, "dreaming plugin not discovered/loaded"
    assert lp.error is None, f"dreaming register() errored: {lp.error}"
    hooks = set(getattr(lp, "hooks_registered", []) or [])
    assert {"on_session_start", "on_session_end"} <= hooks
    assert "dream" in mgr._plugin_commands           # /dream slash command
    assert "dream" in mgr._cli_commands              # hermes dream
    assert "dreaming" in mgr._aux_tasks              # auxiliary.dreaming task


def test_proactivity_loads_clean_through_real_manager():
    mgr = _load_with({"dreaming", "proactivity"})
    lp = _loaded(mgr, "proactivity")
    assert lp is not None, "proactivity plugin not discovered/loaded"
    assert lp.error is None, f"proactivity register() errored: {lp.error}"
    assert "pre_llm_call" in (getattr(lp, "hooks_registered", []) or [])
    assert "track" in mgr._plugin_commands           # /track
    assert "proactivity" in mgr._plugin_commands     # /proactivity
    assert "proactivity" in mgr._cli_commands        # hermes proactivity
    assert len(mgr._hooks.get("pre_llm_call", [])) >= 1


def test_standalone_plugins_are_opt_in():
    # With nothing enabled, the standalone plugins are DISCOVERED but NOT activated:
    # register() must not run, so no hooks/commands of theirs go live. This is what
    # guarantees they cannot affect existing v2 behavior unless opted in.
    mgr = _load_with(set())

    d = mgr._plugins.get("dreaming")
    pr = mgr._plugins.get("proactivity")
    # If present, each must be an inert (un-registered) entry.
    if d is not None:
        assert (getattr(d, "hooks_registered", []) or []) == []
        assert d.error and "not enabled" in d.error.lower()
    if pr is not None:
        assert (getattr(pr, "hooks_registered", []) or []) == []
        assert pr.error and "not enabled" in pr.error.lower()

    # Their unique commands must NOT be registered (proves register() didn't run).
    assert "dream" not in mgr._plugin_commands
    assert "track" not in mgr._plugin_commands
