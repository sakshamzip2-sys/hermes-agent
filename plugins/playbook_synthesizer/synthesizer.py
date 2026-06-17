"""Synthesize recurring patterns into reusable skills — the DREAM→EVOLVE link.

The piece that closes the self-evolution loop: when the agent keeps doing the same
multi-step thing (a workflow, a fix, a pitfall it learned to avoid), this turns that
pattern into a durable SKILL.md — the "descent-playbook.md" behavior v2 lacked. The new
skill then guides future turns, which score better (SENSE), which the loop consolidates.

Capability at the edge: the synthesizer renders a candidate to SKILL.md and creates it via
the existing ``tools.skill_manager_tool`` (agent-created, versioned, curator-managed). It is
idempotent (won't recreate an existing skill) and default-OFF.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("hermes.plugins.playbook_synthesizer")


@dataclass(frozen=True)
class PlaybookCandidate:
    """A recurring pattern proposed for skill-ification."""

    name: str                       # kebab-case skill name
    description: str                # the SKILL.md trigger description (when to use)
    steps: list[str] = field(default_factory=list)  # ordered playbook steps
    evidence: list[str] = field(default_factory=list)  # source facts/sessions
    recurrence: int = 1             # how many times the pattern was observed


# A skill is only worth synthesizing if it's a genuine multi-step pattern that recurred.
_MIN_STEPS = 2
_MIN_RECURRENCE = 2


def slugify(name: str) -> str:
    """Kebab-case a human name for use as a skill directory/name."""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def render_skill_md(candidate: PlaybookCandidate) -> str:
    """Render a valid SKILL.md (frontmatter + body) from a candidate.

    Synthesized skills are self-identifying: the body records that the agent created
    them from a recurring pattern, with the observation count, so they're auditable and
    the curator/human can tell hand-written skills from machine-learned ones.
    """
    slug = slugify(candidate.name)
    desc = " ".join(candidate.description.strip().split())
    # Frontmatter description must be single-line; keep it a clean trigger sentence.
    lines = [
        "---",
        f"name: {slug}",
        f"description: {desc}",
        "---",
        "",
        f"# {candidate.name.strip()}",
        "",
        "> _Synthesized by the agent from a recurring pattern "
        f"(observed {candidate.recurrence}×). Auto-created; safe to edit or remove._",
        "",
        "## When to use",
        "",
        desc + ".",
        "",
        "## Steps",
        "",
    ]
    for i, step in enumerate(candidate.steps, 1):
        lines.append(f"{i}. {step.strip()}")
    if candidate.evidence:
        lines += ["", "## Why this exists", "",
                  "This pattern recurred across sessions:"]
        for ev in candidate.evidence[:5]:
            lines.append(f"- {ev.strip()}")
    lines.append("")
    return "\n".join(lines)


def synthesize(
    candidate: PlaybookCandidate,
    *,
    creator_fn: Callable[..., str],
    exists_fn: Callable[[str], bool],
    category: Optional[str] = None,
) -> dict:
    """Create a skill from ``candidate`` if it's substantial and not already present.

    Idempotent (skips when ``exists_fn(slug)`` is True) and quality-gated (skips thin
    one-off patterns). ``creator_fn`` is the skill-creating callable (defaults to
    ``skill_manage`` in the plugin wiring) — injected here so this is pure + testable.
    """
    slug = slugify(candidate.name)
    if len(candidate.steps) < _MIN_STEPS or candidate.recurrence < _MIN_RECURRENCE:
        return {"created": False, "reason": "too_thin", "name": slug}
    if exists_fn(slug):
        return {"created": False, "reason": "exists", "name": slug}
    content = render_skill_md(candidate)
    try:
        result = creator_fn(name=slug, content=content, category=category)
    except Exception as exc:  # noqa: BLE001 — synthesis must never crash the cycle
        logger.warning("playbook_synthesizer: create failed for %s: %s", slug, exc)
        return {"created": False, "reason": f"error:{type(exc).__name__}", "name": slug}
    return {"created": True, "reason": "created", "name": slug, "result": result}
