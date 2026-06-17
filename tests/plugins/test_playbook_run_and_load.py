"""run() orchestration + real-PluginManager load for the playbook synthesizer."""

from __future__ import annotations

import hermes_cli.plugins as P
from plugins.playbook_synthesizer import run
from plugins.playbook_synthesizer.config import PlaybookConfig
from plugins.playbook_synthesizer.synthesizer import PlaybookCandidate


def _cand(name, **over):
    base = dict(
        name=name,
        description=f"when doing {name}",
        steps=["step one", "step two", "step three"],
        evidence=["seen in A", "seen in B"],
        recurrence=3,
    )
    base.update(over)
    return PlaybookCandidate(**base)


def _cfg(**over):
    base = dict(enabled=True, max_per_cycle=3, category="learned")
    base.update(over)
    return PlaybookConfig(**base)


def test_run_disabled_is_noop() -> None:
    out = run([_cand("a")], config=_cfg(enabled=False),
              creator_fn=lambda **k: "x", exists_fn=lambda n: False)
    assert out["created"] == []
    assert out["reason"] == "disabled"


def test_run_creates_each_new_candidate() -> None:
    made = []
    out = run(
        [_cand("alpha-flow"), _cand("beta-flow")],
        config=_cfg(),
        creator_fn=lambda *, name, content, category: made.append(name) or "ok",
        exists_fn=lambda n: False,
    )
    assert set(out["created"]) == {"alpha-flow", "beta-flow"}
    assert made == ["alpha-flow", "beta-flow"]


def test_run_respects_max_per_cycle() -> None:
    out = run(
        [_cand("a-flow"), _cand("b-flow"), _cand("c-flow")],
        config=_cfg(max_per_cycle=1),
        creator_fn=lambda **k: "ok",
        exists_fn=lambda n: False,
    )
    assert len(out["created"]) == 1
    assert any(s["reason"] == "cap" for s in out["skipped"])


def test_run_skips_existing() -> None:
    out = run(
        [_cand("dup-flow")],
        config=_cfg(),
        creator_fn=lambda **k: (_ for _ in ()).throw(AssertionError("should not create")),
        exists_fn=lambda n: True,
    )
    assert out["created"] == []
    assert out["skipped"][0]["reason"] == "exists"


# --- real loader ------------------------------------------------------------
def _load_with(enabled: set) -> P.PluginManager:
    mgr = P.PluginManager()
    original = P._get_enabled_plugins
    P._get_enabled_plugins = lambda: enabled
    try:
        mgr.discover_and_load(force=True)
    finally:
        P._get_enabled_plugins = original
    return mgr


def test_playbook_loads_through_real_manager() -> None:
    mgr = _load_with({"playbook_synthesizer"})
    plugins = mgr._plugins
    lp = plugins.get("playbook_synthesizer") if isinstance(plugins, dict) else None
    assert lp is not None, "playbook_synthesizer not discovered"
    assert lp.error is None, f"register() errored: {lp.error}"
    assert "playbook" in mgr._plugin_commands
    assert "playbook" in mgr._cli_commands
