"""Per-profile dreaming routing (the gallery-agent dreaming gap fix).

These tests pin the TRIGGER/iteration contribution — that a dream cycle runs for
the global home AND each gallery-agent profile, each scoped to that profile's
HERMES_HOME via the ContextVar override — independent of the (separately tested,
LLM-driven) dream pipeline, which is mocked here.
"""

import asyncio

from plugins.dreaming import runner
from plugins.dreaming.runner import run_dream_cycle_all_profiles
from plugins.dreaming.engine import DreamRunSummary
from hermes_constants import get_hermes_home_override


def _mk_profile(base, slug, with_db=True):
    d = base / "agent-profiles" / slug
    d.mkdir(parents=True, exist_ok=True)
    if with_db:
        (d / "state.db").write_text("x", encoding="utf-8")
    return d


def test_runs_global_plus_each_profile_with_state_db(tmp_path, monkeypatch):
    _mk_profile(tmp_path, "finance")
    _mk_profile(tmp_path, "deep-research")
    _mk_profile(tmp_path, "neverchatted", with_db=False)  # no state.db -> skipped
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    seen = []

    async def fake_cycle(*, force=False, config=None, db_path=None, store=None):
        # Capture the active HERMES_HOME override at call time.
        seen.append(get_hermes_home_override())
        return DreamRunSummary()

    monkeypatch.setattr(runner, "run_dream_cycle", fake_cycle)

    asyncio.run(run_dream_cycle_all_profiles(force=True))

    # 1 global (no override) + 2 profiles WITH a state.db; 'neverchatted' skipped.
    assert len(seen) == 3, seen
    assert seen[0] is None, "first cycle must be the global home (no override)"
    overrides = [str(o) for o in seen[1:] if o]
    assert any(o.endswith("agent-profiles/finance") for o in overrides), overrides
    assert any(o.endswith("agent-profiles/deep-research") for o in overrides), overrides
    assert not any("neverchatted" in (str(o) or "") for o in seen)


def test_override_is_reset_after_each_profile(tmp_path, monkeypatch):
    _mk_profile(tmp_path, "finance")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    async def fake_cycle(*, force=False, config=None, db_path=None, store=None):
        return DreamRunSummary()

    monkeypatch.setattr(runner, "run_dream_cycle", fake_cycle)
    asyncio.run(run_dream_cycle_all_profiles(force=True))

    # After the run, no override may leak into the caller's context.
    assert get_hermes_home_override() is None


def test_no_profiles_still_runs_global(tmp_path, monkeypatch):
    # No agent-profiles dir at all -> just the global cycle, no crash.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    calls = {"n": 0}

    async def fake_cycle(*, force=False, config=None, db_path=None, store=None):
        calls["n"] += 1
        return DreamRunSummary()

    monkeypatch.setattr(runner, "run_dream_cycle", fake_cycle)
    asyncio.run(run_dream_cycle_all_profiles(force=True))
    assert calls["n"] == 1
