"""Tests for the /api/self-evolution backend aggregator (real temp stores)."""

from __future__ import annotations

from pathlib import Path

from gateway.platforms.self_evolution_aggregator import build_self_evolution_payload
from plugins.outcomes.store import OutcomesStore


def _seed_outcomes(home: Path) -> None:
    s = OutcomesStore(home / "dreaming" / "outcomes.db")
    s.record(session_id="A", turn="1", turn_score=0.4, composite=0.4, ts=1.0)
    s.record(session_id="A", turn="2", turn_score=0.8, composite=0.6, judge=0.9, ts=2.0)


def _seed_memory(home: Path) -> None:
    mem = home / "memories"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "MEMORY.md").write_text(
        "Hand-written fact.\n§\n(dreamed 2026-06-17) User prefers Rust.\n§\n"
        "(dreamed 2026-06-17) Uses zsh.",
        encoding="utf-8",
    )


def _seed_skill(home: Path) -> None:
    d = home / "skills" / "learned" / "deploy-flow"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: deploy-flow\ndescription: when deploying\n---\n\n"
        "# Deploy flow\n\n> _Synthesized by the agent from a recurring pattern (observed 3×)._\n",
        encoding="utf-8",
    )


def test_empty_home_is_safe(tmp_path) -> None:
    p = build_self_evolution_payload(tmp_path)
    assert p["outcomes"]["recorded"] == 0
    assert p["review"]["pending_count"] == 0
    assert p["skills"]["synthesized_count"] == 0


def test_outcomes_section_reports_trend_and_mean(tmp_path) -> None:
    _seed_outcomes(tmp_path)
    p = build_self_evolution_payload(tmp_path)
    o = p["outcomes"]
    assert o["recorded"] == 2
    assert abs(o["mean_recent"] - 0.6) < 1e-9  # mean(0.4, 0.8)
    assert o["trend"] == [0.4, 0.8]  # oldest-first for the chart


def test_dreaming_section_lists_dreamed_promotions(tmp_path) -> None:
    _seed_memory(tmp_path)
    p = build_self_evolution_payload(tmp_path)
    d = p["dreaming"]
    assert d["promotion_count"] == 2
    assert any("Rust" in e for e in d["recent_promotions"])
    # Hand-written (non-dreamed) entries are NOT counted as promotions.
    assert not any("Hand-written" in e for e in d["recent_promotions"])


def test_review_section_reads_the_queue(tmp_path) -> None:
    from plugins.dreaming import review

    review.queue_pending(tmp_path / "dreaming", text="A pending fact.", source_event_id="e1",
                         score=0.9, recall_count=3, diversity_score=0.1, now_ns=1)
    p = build_self_evolution_payload(tmp_path)
    r = p["review"]
    assert r["pending_count"] == 1
    assert r["pending"][0]["text"] == "A pending fact."
    assert r["chain_ok"] is True


def test_skills_section_finds_synthesized(tmp_path) -> None:
    _seed_skill(tmp_path)
    p = build_self_evolution_payload(tmp_path)
    s = p["skills"]
    assert s["synthesized_count"] == 1
    assert s["synthesized"][0]["name"] == "deploy-flow"


def test_full_payload_shape(tmp_path) -> None:
    _seed_outcomes(tmp_path)
    _seed_memory(tmp_path)
    _seed_skill(tmp_path)
    p = build_self_evolution_payload(tmp_path)
    assert set(p.keys()) == {"outcomes", "dreaming", "review", "skills"}
    assert all(isinstance(v, dict) for v in p.values())
