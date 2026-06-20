"""Tests for Part 2 / Slice 5 — utility view + controlled entity_type vocab.

Two additive, read-only-rollup features, proven against real stores in temp dirs
(never the live ~/.hermes):

PART A — ``tools/memory_utility.utility_view``:
  (a) a high-use + high-rating skill ranks ABOVE a low-use + low-rating one;
  (b) the view is READ-ONLY — calling it writes nothing to the skill sidecar;
  (c) all three sort orders (most_useful / least_useful / decaying) work and are
      self-consistent (decaying mirrors least_useful since decay = 1 - utility).

PART B — ``plugins/memory/holographic/store`` typed entities:
  (d) an entity can be typed company / client / project and queried by type;
  (e) an invalid type is coerced to ``'other'`` (never crashes);
  (f) legacy untyped entities stay ``'unknown'`` and still work, and the classify
      hook types a company-suffixed name without an explicit type.

No new HERMES_* env var is introduced; the skill fixture only repoints the
existing HERMES_HOME at a tmp dir (mirrors tests/plugins/test_skill_health.py).
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from plugins.memory.holographic.store import (
    ENTITY_TYPES,
    ENTITY_TYPE_OTHER,
    ENTITY_TYPE_UNKNOWN,
    MemoryStore,
    classify_entity_type,
    normalize_entity_type,
)


# ===========================================================================
# PART A — utility_view
# ===========================================================================


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with a clean skills/ dir, reloaded per test.

    Mirrors tests/plugins/test_skill_health.py. Pins ``curator.prune_builtins``
    OFF so usage telemetry is recorded for any name without provenance gating,
    and the fact plane is disabled by default so utility_view sees ONLY the
    skills we seed (deterministic).
    """
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "skills").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import tools.skill_usage as su
    importlib.reload(su)
    monkeypatch.setattr(su, "_prune_builtins_enabled", lambda: False)
    return home


def _write_skill(skills_dir: Path, name: str) -> None:
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill\n---\n\n# body\n",
        encoding="utf-8",
    )


def _seed_high_and_low(home: Path):
    """Seed a high-use+high-rating skill and a low-use+low-rating one."""
    import tools.skill_usage as su

    skills_dir = home / "skills"
    _write_skill(skills_dir, "winner")
    _write_skill(skills_dir, "loser")

    # winner: used a lot, rated well.
    for _ in range(10):
        su.bump_use("winner")
    su.record_skill_outcome("winner", turn_score=0.95, user_rating=1.0)

    # loser: used once, rated poorly.
    su.bump_use("loser")
    su.record_skill_outcome("loser", turn_score=0.1, user_rating=0.0)


def test_utility_view_ranks_high_use_high_rating_above_low(skills_home):
    """(a) high-use + high-rating skill outranks the low one."""
    _seed_high_and_low(skills_home)
    import tools.memory_utility as mu

    rows = mu.utility_view(include_facts=False)
    by_key = {r["key"]: r for r in rows}
    assert "winner" in by_key and "loser" in by_key
    assert by_key["winner"]["utility"] > by_key["loser"]["utility"]

    ranked = mu.sort_utility(rows, "most_useful")
    names = [r["key"] for r in ranked]
    # winner must come strictly before loser in the most_useful ordering.
    assert names.index("winner") < names.index("loser")


def test_utility_view_is_read_only(skills_home):
    """(b) computing the view writes nothing to the sidecar."""
    _seed_high_and_low(skills_home)
    import tools.skill_usage as su
    import tools.memory_utility as mu

    usage_path = su._usage_file()
    before = usage_path.read_text(encoding="utf-8")
    snapshot = json.loads(before)

    # Call the view twice — neither call may mutate the sidecar.
    mu.utility_view(include_facts=False)
    mu.sort_utility(mu.utility_view(include_facts=False), "decaying")

    after = usage_path.read_text(encoding="utf-8")
    assert json.loads(after) == snapshot, "utility_view must not write to the sidecar"


def test_utility_view_three_sort_orders(skills_home):
    """(c) most_useful / least_useful / decaying all work and are consistent."""
    _seed_high_and_low(skills_home)
    import tools.memory_utility as mu

    rows = mu.utility_view(include_facts=False)

    most = [r["key"] for r in mu.sort_utility(rows, "most_useful")]
    least = [r["key"] for r in mu.sort_utility(rows, "least_useful")]
    decaying = [r["key"] for r in mu.sort_utility(rows, "decaying")]

    # most_useful is the reverse intent of least_useful.
    assert most[0] == "winner"
    assert least[0] == "loser"
    # decay = 1 - utility, so decaying order == least_useful order.
    assert decaying == least
    # An unknown order falls back to most_useful (no crash).
    assert [r["key"] for r in mu.sort_utility(rows, "bogus")] == most
    # sort_utility does not mutate its input.
    assert [r["key"] for r in rows] == [r["key"] for r in mu.utility_view(include_facts=False)]


def test_utility_scoring_helpers_monotonic():
    """utility rises with use and with helpfulness; decay is its complement."""
    import tools.memory_utility as mu

    # More uses -> higher utility at fixed helpfulness.
    assert mu._utility(10, 1.0) > mu._utility(1, 1.0)
    # Higher helpfulness -> higher utility at fixed use.
    assert mu._utility(5, 1.0) > mu._utility(5, 0.1)
    # decay is exactly the complement of utility.
    assert abs(mu._decay(5, 0.8) - (1.0 - mu._utility(5, 0.8))) < 1e-9
    # Never-used item has zero utility regardless of rating.
    assert mu._utility(0, 1.0) == 0.0


# ===========================================================================
# PART B — controlled entity_type vocabulary
# ===========================================================================


@pytest.fixture()
def store(tmp_path):
    s = MemoryStore(db_path=str(tmp_path / "memory_store.db"))
    try:
        yield s
    finally:
        s.close()


def test_entity_typed_company_client_project_queryable(store):
    """(d) entities typed company/client/project are queryable by type."""
    assert store.set_entity_type("Acme Holdings", "company") == "company"
    assert store.set_entity_type("Globex", "client") == "client"
    assert store.set_entity_type("Apollo", "project") == "project"

    companies = [e["name"] for e in store.entities_by_type("company")]
    clients = [e["name"] for e in store.entities_by_type("client")]
    projects = [e["name"] for e in store.entities_by_type("project")]

    assert "Acme Holdings" in companies
    assert "Globex" in clients
    assert "Apollo" in projects
    # Cross-type isolation: a company is not returned under client.
    assert "Acme Holdings" not in clients
    assert store.get_entity_type("Acme Holdings") == "company"


def test_invalid_entity_type_coerced_to_other(store):
    """(e) an unrecognized type is coerced to 'other', never rejected."""
    # Pure-function contract.
    assert normalize_entity_type("frobnicator") == ENTITY_TYPE_OTHER
    assert normalize_entity_type("") == ENTITY_TYPE_UNKNOWN
    assert normalize_entity_type(None) == ENTITY_TYPE_UNKNOWN
    assert normalize_entity_type("COMPANY") == "company"  # case-insensitive
    assert ENTITY_TYPE_OTHER in ENTITY_TYPES and ENTITY_TYPE_UNKNOWN in ENTITY_TYPES

    # End-to-end: an invalid type lands as 'other' in the store and is queryable.
    written = store.set_entity_type("Mystery Thing", "not-a-real-type")
    assert written == ENTITY_TYPE_OTHER
    assert store.get_entity_type("Mystery Thing") == ENTITY_TYPE_OTHER
    others = [e["name"] for e in store.entities_by_type("other")]
    assert "Mystery Thing" in others
    # Querying an invalid type folds into 'other' too (symmetric round-trip).
    assert [e["name"] for e in store.entities_by_type("garbage")] == others


def test_legacy_untyped_entities_default_unknown(store):
    """(f) entities created the legacy way (via add_fact) stay 'unknown'."""
    # add_fact auto-extracts capitalized multi-word entities (legacy path).
    store.add_fact("John Smith joined the team", category="people")
    assert store.get_entity_type("John Smith") == ENTITY_TYPE_UNKNOWN
    # A never-stored entity returns None (no row created on read).
    assert store.get_entity_type("Nobody Here") is None

    # Typing an existing legacy entity upgrades it in place.
    assert store.set_entity_type("John Smith", "person") == "person"
    assert store.get_entity_type("John Smith") == "person"
    assert "John Smith" in [e["name"] for e in store.entities_by_type("person")]


def test_classify_hook_types_company_suffix(store):
    """(f) the optional classify hook types a company-suffixed name."""
    # Pure heuristic contract.
    assert classify_entity_type("Initech LLC") == "company"
    assert classify_entity_type("Stark Industries Inc.") == "company"
    assert classify_entity_type("our biggest client", "from the customer call") == "client"
    assert classify_entity_type("the Q3 project plan") == "project"
    # No cue -> unknown, leaving the caller in control.
    assert classify_entity_type("Banana") == ENTITY_TYPE_UNKNOWN

    # set_entity_type(classify=True) with no explicit type uses the heuristic.
    written = store.set_entity_type("Umbrella Corp", classify=True)
    assert written == "company"
    assert "Umbrella Corp" in [e["name"] for e in store.entities_by_type("company")]
    # set_entity_type with no type and no classify defaults to unknown.
    assert store.set_entity_type("Plain Name") == ENTITY_TYPE_UNKNOWN
