"""Tests for the DREAM→EVOLVE playbook synthesizer (pure render + idempotent create)."""

from __future__ import annotations

from plugins.playbook_synthesizer.synthesizer import (
    PlaybookCandidate,
    render_skill_md,
    slugify,
    synthesize,
)


def _cand(**over):
    base = dict(
        name="Deploy The Widget Service",
        description="When deploying the widget service to staging",
        steps=["Run the migration", "Flip the feature flag", "Smoke-test /health"],
        evidence=["session A: deploy failed on migration", "session B: same fix worked"],
        recurrence=3,
    )
    base.update(over)
    return PlaybookCandidate(**base)


def test_slugify_makes_kebab_case() -> None:
    assert slugify("Deploy The Widget Service") == "deploy-the-widget-service"
    assert slugify("Fix: weird   spacing!!") == "fix-weird-spacing"
    assert slugify("already-kebab") == "already-kebab"


def test_render_skill_md_has_valid_frontmatter() -> None:
    md = render_skill_md(_cand())
    assert md.startswith("---\n")
    assert "name: deploy-the-widget-service" in md
    assert "description:" in md
    # The steps appear in the body, in order.
    assert "Run the migration" in md
    assert md.index("Run the migration") < md.index("Flip the feature flag")


def test_render_includes_provenance() -> None:
    md = render_skill_md(_cand())
    # Synthesized skills must be self-identifying (auditable origin).
    assert "synthesized" in md.lower()
    assert "recurrence" in md.lower() or "observed" in md.lower()


def test_synthesize_creates_when_absent() -> None:
    created = {}

    def creator_fn(*, name, content, category):  # noqa: ANN001
        created["name"] = name
        created["content"] = content
        return '{"success": true}'

    res = synthesize(_cand(), creator_fn=creator_fn, exists_fn=lambda n: False)
    assert res["created"] is True
    assert created["name"] == "deploy-the-widget-service"
    assert "Run the migration" in created["content"]


def test_synthesize_is_idempotent() -> None:
    def creator_fn(**kw):  # noqa: ANN003
        raise AssertionError("must not create an already-existing skill")

    res = synthesize(_cand(), creator_fn=creator_fn, exists_fn=lambda n: True)
    assert res["created"] is False
    assert res["reason"] == "exists"


def test_synthesize_skips_thin_candidates() -> None:
    # A 1-step "pattern" with recurrence 1 isn't worth a skill.
    thin = _cand(steps=["do the thing"], recurrence=1)

    def creator_fn(**kw):  # noqa: ANN003
        raise AssertionError("must not create a thin candidate")

    res = synthesize(thin, creator_fn=creator_fn, exists_fn=lambda n: False)
    assert res["created"] is False
    assert res["reason"] == "too_thin"
