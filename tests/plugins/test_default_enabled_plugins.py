"""Ship-default plugin loadout.

OpenComputer ships a curated set of plugins enabled by default
(:data:`hermes_cli.plugins.DEFAULT_ENABLED_PLUGINS`) so that any clone / fork
inherits the full model-provider + image/video + observability + platform +
parallel-agents loadout without per-user configuration.

These tests guarantee:
  1. the loadout the project commits to is actually in the default set,
  2. a fresh clone (no plugins config) gets the defaults as the floor,
  3. user config is unioned on top (never replaces the defaults),
  4. every default key maps to a REAL discoverable bundled plugin — so a
     typo'd key can never silently ship a never-loading default,
  5. the gated standalone defaults actually load through the real loader.
"""

from __future__ import annotations

import hermes_cli.config as C
import hermes_cli.plugins as P

# The loadout this project commits to shipping enabled. A subset of
# DEFAULT_ENABLED_PLUGINS chosen because these are the ones a regression would
# most likely silently drop (providers, platforms, observability, gen backends).
SHIPPED_LOADOUT = {
    "model-providers/anthropic",
    "model-providers/custom",
    "model-providers/openai-codex",
    "model-providers/xai",
    "image_gen/openai",
    "image_gen/openai-codex",
    "image_gen/xai",
    "video_gen/xai",
    "observability/langfuse",
    "security-guidance",
    "platforms/discord",
    "platforms/irc",
}


def test_shipped_loadout_is_in_default_set():
    missing = sorted(k for k in SHIPPED_LOADOUT if k not in P.DEFAULT_ENABLED_PLUGINS)
    assert not missing, f"shipped loadout not in DEFAULT_ENABLED_PLUGINS: {missing}"


def test_fresh_clone_gets_defaults_as_floor(monkeypatch):
    # Simulate a brand-new clone: no plugins config whatsoever.
    monkeypatch.setattr(C, "load_config", lambda: {})
    effective = P._get_enabled_plugins()
    assert isinstance(effective, set)
    assert P.DEFAULT_ENABLED_PLUGINS <= effective


def test_empty_plugins_block_still_gets_defaults(monkeypatch):
    # An explicit-but-empty enabled list must NOT wipe the shipped defaults.
    monkeypatch.setattr(C, "load_config", lambda: {"plugins": {"enabled": []}})
    effective = P._get_enabled_plugins()
    assert P.DEFAULT_ENABLED_PLUGINS <= effective


def test_user_enabled_unions_with_defaults(monkeypatch):
    monkeypatch.setattr(
        C, "load_config", lambda: {"plugins": {"enabled": ["user/custom-plugin"]}}
    )
    effective = P._get_enabled_plugins()
    assert "user/custom-plugin" in effective
    assert P.DEFAULT_ENABLED_PLUGINS <= effective


def test_every_default_key_maps_to_a_real_plugin():
    """Every default key must be a real plugin per the SAME discovery oracle that
    ``oc plugins enable``/``oc plugins list`` use (path-derived registry keys).

    This is the authoritative check: DEFAULT_ENABLED_PLUGINS uses config-style
    path keys (e.g. ``model-providers/anthropic``), so a typo'd key — one that
    no real plugin would ever match — fails here.
    """
    from hermes_cli.plugins_cmd import _discover_all_plugins

    universe: set[str] = set()
    for entry in _discover_all_plugins():
        # entry = (name, version, description, source, dir, key)
        name, key = entry[0], entry[5]
        if name:
            universe.add(name)
        if key:
            universe.add(key)

    missing = sorted(k for k in P.DEFAULT_ENABLED_PLUGINS if k not in universe)
    assert not missing, f"default keys with no matching bundled plugin: {missing}"


def test_gated_standalone_defaults_load_through_real_loader():
    """security-guidance is a standalone plugin gated by the allow-list; with the
    shipped defaults it must actually load (not be skipped as 'not enabled')."""
    mgr = P.PluginManager()
    mgr.discover_and_load(force=True)

    plugins = mgr._plugins
    items = (
        plugins.items()
        if isinstance(plugins, dict)
        else [(None, lp) for lp in plugins]
    )
    by_name = {}
    for key, lp in items:
        manifest = getattr(lp, "manifest", None)
        nm = getattr(manifest, "name", None) or key
        by_name[nm] = lp
        if key:
            by_name[key] = lp

    sg = by_name.get("security-guidance")
    assert sg is not None, "security-guidance not discovered"
    assert getattr(sg, "enabled", False) is True, (
        f"security-guidance should load as a shipped default, got error: "
        f"{getattr(sg, 'error', None)}"
    )
