"""Tests for the reflection PROPOSAL pass (Slice 4).

The reflection pass PROPOSES; it NEVER auto-applies. These tests use a STUB llm (no
network), TEMP paths (never the real PROPOSALS.md / ~/.hermes), and prove the four
contract guarantees:

  (a) given low-scoring outcome rows, the pass writes proposals to a temp PROPOSALS.md
      AND queues them in a temp HMAC review queue (verify_chain True);
  (b) it makes ZERO writes to any skill file / MEMORY.md / fact store (the skill +
      memory dirs are byte-for-byte untouched);
  (c) the default config has reflection disabled -> the pass is a no-op;
  (d) it is idempotent: re-running over the same signals queues no duplicate proposals.

Everything is hermetic: the outcomes db, PROPOSALS.md, and the review home all live under
tmp_path.
"""

from __future__ import annotations

import asyncio
import json

from plugins.dreaming import reflection, review
from plugins.outcomes.store import OutcomesStore


def _run(coro):
    return asyncio.run(coro)


# A deterministic stub LLM: echoes one proposal per signal it is given. No network.
def _stub_llm(reply_objs=None):
    async def llm(system, user):  # noqa: ANN001
        # The user prompt embeds the signals JSON; mirror back a valid proposal per signal.
        if reply_objs is not None:
            return json.dumps(reply_objs)
        # Parse the signal ids out of the embedded JSON and propose one rule each.
        start = user.find("[")
        end = user.rfind("]")
        sigs = json.loads(user[start : end + 1]) if start != -1 and end != -1 else []
        objs = [
            {
                "signal_id": s["signal_id"],
                "rule": f"Address pattern: {s['summary']}",
                "risk": "May not generalise beyond sampled runs.",
                "target": "workflow",
                "revert": "Reject in dream review.",
            }
            for s in sigs
        ]
        return json.dumps(objs)

    return llm


def _seed_low_outcomes(db_path, *, n_agent_atlas=4) -> None:
    """Seed turn_outcomes with several low-scoring rows under a poor agent."""
    s = OutcomesStore(db_path)
    for i in range(n_agent_atlas):
        s.record(
            session_id="sess-low",
            turn=i,
            turn_score=0.20,
            ts=float(i),
            trajectory="tools: 0 ok / 2 err; retries=2",
            agent_id="atlas",
        )
    # One healthy row so the store isn't uniformly bad (and to prove filtering works).
    s.record(session_id="sess-ok", turn=99, turn_score=0.95, ts=100.0, agent_id="forge")


def _enabled_cfg(**over) -> reflection.ReflectionConfig:
    base: dict[str, object] = {
        "enabled": True,
        "score_below": 0.5,
        "min_low_runs": 3,
        "max_proposals": 5,
        "low_fetch_limit": 50,
    }
    base.update(over)
    return reflection.ReflectionConfig(**base)  # type: ignore[arg-type]


# (a) low rows -> proposals appended to temp PROPOSALS.md + queued (chain verifies) ----
def test_low_rows_produce_proposals_and_queue(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    props.write_text("# PROPOSALS.md\n\n## Proposals\n\n(none yet.)\n", encoding="utf-8")
    review_home = tmp_path / "dreaming"

    result = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=1_000,
        )
    )

    assert result.enabled is True
    assert len(result.proposed) >= 1

    # PROPOSALS.md was APPENDED to (original header preserved) and now carries the entry.
    text = props.read_text(encoding="utf-8")
    assert "# PROPOSALS.md" in text  # original content intact
    assert "## Proposals" in text
    assert "status: proposed" in text
    assert "signal-id: REF-" in text
    for sid in result.proposed:
        assert sid in text

    # The HMAC review queue has the same proposals and the chain verifies.
    state = review.load_state(review_home)
    assert len(state.items) == len(result.proposed)
    assert review.verify_chain(review_home) is True
    queued_ids = {it.source_event_id for it in state.items}
    assert queued_ids == set(result.proposed)
    # Queued text is the human-readable rule, flagged as a reflection proposal.
    assert all(it.text.startswith("[reflection proposal]") for it in state.items)


# (b) ZERO writes to any skill file / MEMORY.md / fact store --------------------------
def test_makes_zero_writes_to_skills_or_memory(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    # Build sentinel skill + memory dirs and snapshot their exact contents.
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    skill_file = skills_dir / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("name: demo\n", encoding="utf-8")
    memory_md = tmp_path / "MEMORY.md"
    memory_md.write_text("- existing fact\n", encoding="utf-8")
    user_md = tmp_path / "USER.md"
    user_md.write_text("- existing pref\n", encoding="utf-8")
    facts_db = tmp_path / "facts.db"
    facts_db.write_text("FACTS", encoding="utf-8")

    def _snapshot(p):
        return {f: f.read_bytes() for f in p.rglob("*") if f.is_file()}

    skills_before = _snapshot(skills_dir)
    mem_before = memory_md.read_bytes()
    user_before = user_md.read_bytes()
    facts_before = facts_db.read_bytes()

    _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=2_000,
        )
    )

    # Skill files, MEMORY.md, USER.md, and the fact store are byte-for-byte untouched.
    assert _snapshot(skills_dir) == skills_before
    assert memory_md.read_bytes() == mem_before
    assert user_md.read_bytes() == user_before
    assert facts_db.read_bytes() == facts_before

    # The ONLY mutations are PROPOSALS.md (created) and the review queue (created).
    assert props.exists()
    assert (review_home / "pending_promotions.json").exists()


def test_zero_writes_even_when_db_outside_tmp_is_never_written(tmp_path, monkeypatch) -> None:
    """Hard guard: if anything tried to call the LLM's _default path or write memory, the
    test environment would surface it. We also assert the pass touches no module-level
    default paths by forcing them to raise if used."""
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    # If the pass ever reached for the real default proposals path or review home, this
    # would point them at a guarded sentinel; we pass explicit temp paths so it must not.
    def _boom():
        raise AssertionError("reflection reached for a live default path")

    monkeypatch.setattr(reflection, "default_proposals_path", _boom)
    monkeypatch.setattr(reflection, "_default_review_home", _boom)

    result = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=3_000,
        )
    )
    assert result.enabled is True
    assert len(result.proposed) >= 1


# (c) default config is disabled -> the pass is a no-op ------------------------------
def test_default_config_is_disabled() -> None:
    # Empty dreaming block -> reflection sub-block absent -> disabled by default.
    cfg = reflection.load_reflection_config(block={})
    assert cfg.enabled is False
    # And an explicit empty reflection sub-block is still disabled.
    assert reflection.load_reflection_config(block={"reflection": {}}).enabled is False


def test_disabled_pass_is_a_noop(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    # A stub that, if ever called, fails the test (the no-op must not invoke the LLM).
    async def _explode(system, user):  # noqa: ANN001
        raise AssertionError("LLM called while reflection disabled")

    result = _run(
        reflection.run_reflection_pass(
            cfg=reflection.ReflectionConfig(enabled=False),
            llm=_explode,
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=4_000,
        )
    )
    assert result.enabled is False
    assert result.proposed == ()
    # No PROPOSALS.md, no queue file: nothing was written.
    assert not props.exists()
    assert not (review_home / "pending_promotions.json").exists()


def test_enabled_only_when_config_says_so() -> None:
    cfg = reflection.load_reflection_config(block={"reflection": {"enabled": True}})
    assert cfg.enabled is True


# (d) idempotent: re-running over the same signals adds no duplicates ----------------
def test_rerun_is_idempotent_no_duplicates(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    props.write_text("# PROPOSALS.md\n\n## Proposals\n\n", encoding="utf-8")
    review_home = tmp_path / "dreaming"

    first = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=5_000,
        )
    )
    assert len(first.proposed) >= 1
    n_after_first = len(review.load_state(review_home).items)
    text_after_first = props.read_text(encoding="utf-8")

    # Second run over the SAME outcomes: every signal id is already proposed -> skipped.
    second = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=6_000,
        )
    )
    assert second.proposed == ()
    assert set(first.proposed) <= set(second.skipped_existing)

    # No new queue items and no new PROPOSALS.md entries were appended.
    assert len(review.load_state(review_home).items) == n_after_first
    assert props.read_text(encoding="utf-8") == text_after_first
    # Chain still verifies after the (no-op) second run.
    assert review.verify_chain(review_home) is True


def test_below_min_low_runs_is_a_noop(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    # Only 1 low row but min_low_runs=3 -> skip (not enough signal).
    _seed_low_outcomes(db, n_agent_atlas=1)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    result = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(min_low_runs=3),
            llm=_stub_llm(),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=7_000,
        )
    )
    assert result.enabled is True
    assert result.proposed == ()
    assert not (review_home / "pending_promotions.json").exists()


# signal gathering is deterministic + capped -----------------------------------------
def test_gather_signals_is_deterministic_and_capped() -> None:
    low_rows = [
        {"agent_id": "atlas", "trajectory": "tools: 0 ok / 2 err", "turn_score": 0.2},
        {"agent_id": "atlas", "trajectory": "tools: 0 ok / 2 err", "turn_score": 0.2},
        {"agent_id": "atlas", "trajectory": "tools: 0 ok / 2 err", "turn_score": 0.1},
    ]
    cfg = _enabled_cfg(max_proposals=2, min_low_runs=2)
    a = reflection.gather_signals(
        low_rows=low_rows,
        session_scores=[("s1", 0.2)],
        agent_scores=[("atlas", 0.18)],
        cfg=cfg,
    )
    b = reflection.gather_signals(
        low_rows=low_rows,
        session_scores=[("s1", 0.2)],
        agent_scores=[("atlas", 0.18)],
        cfg=cfg,
    )
    # Deterministic ids across calls; capped at max_proposals.
    assert [s.signal_id for s in a] == [s.signal_id for s in b]
    assert len(a) <= 2
    assert all(s.signal_id.startswith("REF-") for s in a)


def test_llm_returning_bad_json_yields_no_proposals(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    async def _garbage(system, user):  # noqa: ANN001
        return "I cannot help with that."

    result = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_garbage,
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=8_000,
        )
    )
    # Enabled and signals existed, but no parseable proposal -> nothing queued.
    assert result.enabled is True
    assert result.proposed == ()
    assert not (review_home / "pending_promotions.json").exists()


def test_llm_hallucinated_signal_id_is_ignored(tmp_path) -> None:
    db = tmp_path / "outcomes.db"
    _seed_low_outcomes(db)
    props = tmp_path / "PROPOSALS.md"
    review_home = tmp_path / "dreaming"

    # The LLM references a signal id that was never given -> must be dropped.
    bad = [{
        "signal_id": "REF-deadbeefdead",
        "rule": "do something",
        "risk": "x",
        "target": "skill",
        "revert": "y",
    }]
    result = _run(
        reflection.run_reflection_pass(
            cfg=_enabled_cfg(),
            llm=_stub_llm(reply_objs=bad),
            outcomes_db_path=db,
            proposals_path=props,
            review_home=review_home,
            now_ns=9_000,
        )
    )
    assert result.proposed == ()
    assert not (review_home / "pending_promotions.json").exists()
