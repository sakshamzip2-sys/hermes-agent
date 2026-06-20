"""Tests for reward-signals-from-feedback (Part 2, Slice 5 / P2-6c).

Proves the primitive-reinforcement feedback path:
  (1) the action -> bounded-signal mapping is correct (positive vs negative);
  (2) positive actions RAISE a skill's ``user_rating`` running mean and negative
      actions LOWER it (sample-count-weighted, no model);
  (3) an unknown action is a safe no-op (no store write, returns None);
  (4) it NEVER trains or calls a model (no LLM/SFT/DPO/RLHF import or call);
  (5) the optional turn_outcomes ``user_rating`` running mean also folds in.

All tests use a temp HERMES_HOME (never the live ~/.hermes store) and a temp
outcomes DB. No new HERMES_* env vars; the fixture only points the existing
HERMES_HOME at a tmp dir, mirroring tests/plugins/test_skill_health.py.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME with a clean skills/ dir, reloaded per test."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "skills").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    import tools.skill_usage as su

    importlib.reload(su)
    return su


# ---------------------------------------------------------------------------
# (1) action -> signal mapping
# ---------------------------------------------------------------------------

def test_positive_actions_map_to_high_signal() -> None:
    from plugins.outcomes import feedback as fb

    for action in ("copied", "shared", "kept"):
        assert fb.action_to_signal(action) == 1.0
        assert action in fb.POSITIVE_ACTIONS


def test_negative_actions_map_to_low_signal() -> None:
    from plugins.outcomes import feedback as fb

    for action in ("heavy_edit", "regenerate", "discarded"):
        assert fb.action_to_signal(action) == 0.0
        assert action in fb.NEGATIVE_ACTIONS


def test_signal_is_bounded_in_unit_interval() -> None:
    from plugins.outcomes import feedback as fb

    for action in fb.KNOWN_ACTIONS:
        s = fb.action_to_signal(action)
        assert s is not None
        assert 0.0 <= s <= 1.0


def test_mapping_is_case_and_whitespace_insensitive() -> None:
    from plugins.outcomes import feedback as fb

    assert fb.action_to_signal("  COPIED ") == 1.0
    assert fb.action_to_signal("Discarded") == 0.0


def test_unknown_action_maps_to_none() -> None:
    from plugins.outcomes import feedback as fb

    for bad in ("", "  ", "frobnicate", "thumbs_up", None):  # type: ignore[arg-type]
        assert fb.action_to_signal(bad) is None  # type: ignore[arg-type]


def test_positive_and_negative_sets_are_disjoint_and_cover_known() -> None:
    from plugins.outcomes import feedback as fb

    assert fb.POSITIVE_ACTIONS.isdisjoint(fb.NEGATIVE_ACTIONS)
    assert fb.POSITIVE_ACTIONS | fb.NEGATIVE_ACTIONS == fb.KNOWN_ACTIONS


# ---------------------------------------------------------------------------
# (2) positive raises / negative lowers the skill running mean
# ---------------------------------------------------------------------------

def test_positive_feedback_raises_skill_user_rating(skills_home) -> None:
    su = skills_home
    from plugins.outcomes import feedback as fb

    # Seed a low baseline rating, then a positive action must raise the mean.
    su.record_skill_outcome("alpha", user_rating=0.0)
    before = su.get_record("alpha")["user_rating"]
    assert before == 0.0

    signal = fb.record_feedback("copied", skill="alpha")
    assert signal == 1.0

    after = su.get_record("alpha")["user_rating"]
    # Running mean of [0.0, 1.0] == 0.5 -> strictly raised.
    assert after == pytest.approx(0.5)
    assert after > before


def test_negative_feedback_lowers_skill_user_rating(skills_home) -> None:
    su = skills_home
    from plugins.outcomes import feedback as fb

    # Seed a high baseline rating, then a negative action must lower the mean.
    su.record_skill_outcome("beta", user_rating=1.0)
    before = su.get_record("beta")["user_rating"]
    assert before == 1.0

    signal = fb.record_feedback("discarded", skill="beta")
    assert signal == 0.0

    after = su.get_record("beta")["user_rating"]
    # Running mean of [1.0, 0.0] == 0.5 -> strictly lowered.
    assert after == pytest.approx(0.5)
    assert after < before


def test_running_mean_over_multiple_feedback_samples(skills_home) -> None:
    su = skills_home
    from plugins.outcomes import feedback as fb

    # Three positives then one negative: mean of [1,1,1,0] == 0.75.
    for _ in range(3):
        fb.record_feedback("kept", skill="gamma")
    fb.record_feedback("regenerate", skill="gamma")

    rec = su.get_record("gamma")
    assert rec["user_rating"] == pytest.approx(0.75)
    assert rec["sample_count"] == 4


# ---------------------------------------------------------------------------
# (3) unknown action is a safe no-op
# ---------------------------------------------------------------------------

def test_unknown_action_is_safe_noop(skills_home) -> None:
    su = skills_home
    from plugins.outcomes import feedback as fb

    result = fb.record_feedback("frobnicate", skill="delta")
    assert result is None
    # No record was created/touched for the skill.
    rec = su.get_record("delta")
    assert rec["user_rating"] is None
    assert rec["sample_count"] == 0


def test_known_action_without_skill_does_not_raise(skills_home) -> None:
    from plugins.outcomes import feedback as fb

    # No skill and no turn given: still maps the signal, just no sink to write.
    assert fb.record_feedback("copied") == 1.0


# ---------------------------------------------------------------------------
# (4) never trains or calls a model
# ---------------------------------------------------------------------------

def test_record_feedback_never_calls_a_model(skills_home, monkeypatch) -> None:
    """The feedback path is primitive reinforcement only: no LLM, no training.

    We sabotage every plausible model-call seam so that ANY attempt to reach a
    model raises. The full positive+negative+turn path must complete untouched.
    """
    su = skills_home
    from plugins.outcomes import feedback as fb

    def _boom(*_a, **_k):
        raise AssertionError("a model/training path was invoked from record_feedback")

    # Block the aux-LLM judge and any auxiliary client construction.
    import plugins.outcomes.judge as judge_mod

    for name in dir(judge_mod):
        obj = getattr(judge_mod, name)
        if callable(obj) and name.lower().startswith(("judge", "score", "ask", "call")):
            monkeypatch.setattr(judge_mod, name, _boom, raising=False)

    # Block common model entry points if importable; ignore if absent.
    for modpath, attr in (
        ("agent.auxiliary", "get_auxiliary_client"),
        ("agent.model_router", "complete"),
    ):
        try:
            mod = importlib.import_module(modpath)
        except Exception:
            continue
        if hasattr(mod, attr):
            monkeypatch.setattr(mod, attr, _boom, raising=False)

    # Run the whole path; it must not touch any of the sabotaged seams.
    assert fb.record_feedback("copied", skill="zeta") == 1.0
    assert fb.record_feedback("discarded", skill="zeta") == 0.0
    rec = su.get_record("zeta")
    assert rec["sample_count"] == 2
    assert rec["user_rating"] == pytest.approx(0.5)


def test_feedback_module_imports_no_llm_packages() -> None:
    """Static guard: the module imports no training/model frameworks.

    Scans only ``import`` statements (the docstring legitimately NAMES SFT/DPO/RLHF
    to declare them out of scope, so a naive substring scan would false-positive).
    Word-boundary match avoids ``sft`` matching ``soft`` (as in "fail-soft").
    """
    import re

    src = Path("plugins/outcomes/feedback.py").read_text(encoding="utf-8")
    banned = ("sft", "dpo", "rlhf", "torch", "transformers", "finetune", "fine_tune")
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for word in banned:
            if re.search(rf"\b{re.escape(word)}\b", stripped.lower()):
                raise AssertionError(
                    f"feedback.py imports a banned model framework: {word}"
                )
    # Sanity: the docstring DOES state the no-training contract. Normalize
    # whitespace because the phrase may wrap across a line break in the source.
    normalized = " ".join(src.lower().split())
    assert "no model training here" in normalized


# ---------------------------------------------------------------------------
# (5) optional turn_outcomes user_rating running mean
# ---------------------------------------------------------------------------

def test_feedback_folds_into_turn_outcomes_running_mean(skills_home, tmp_path) -> None:
    from plugins.outcomes import feedback as fb
    from plugins.outcomes.store import OutcomesStore

    db = tmp_path / "outcomes.db"
    store = OutcomesStore(db)
    store.record(session_id="S1", turn="t1", turn_score=0.7, ts=1.0)

    # Positive then negative on the same turn -> running mean of [1.0, 0.0] == 0.5.
    s1 = fb.record_feedback("shared", skill=None, session_id="S1", turn="t1", db_path=db)
    s2 = fb.record_feedback("heavy_edit", skill=None, session_id="S1", turn="t1", db_path=db)
    assert (s1, s2) == (1.0, 0.0)

    row = OutcomesStore(db).recent_low_scoring_rows(score_below=1.0, limit=10)[0]
    assert row["user_rating"] == pytest.approx(0.5)
    # The scorer-owned turn_score is never disturbed by feedback.
    assert row["turn_score"] == pytest.approx(0.7)


def test_turn_outcomes_rating_missing_turn_is_noop(skills_home, tmp_path) -> None:
    from plugins.outcomes import feedback as fb
    from plugins.outcomes.store import OutcomesStore

    db = tmp_path / "outcomes.db"
    OutcomesStore(db)  # init schema, no rows
    # No matching turn -> record_user_rating returns False, record_feedback still
    # returns the mapped signal and does not raise.
    assert fb.record_feedback("kept", session_id="nope", turn="x", db_path=db) == 1.0
    assert OutcomesStore(db).record_user_rating(session_id="nope", turn="x", signal=1.0) is False


def test_record_user_rating_on_unmigrated_db_is_safe_noop(tmp_path, monkeypatch) -> None:
    """PRAGMA-guard: an old DB without the user_rating column never crashes.

    We construct a legacy turn_outcomes table (no user_rating column) and patch
    ``_init_schema`` to a no-op so the additive migration never runs, then call
    ``record_user_rating`` directly. The PRAGMA guard must return False without
    raising and without mutating the row.
    """
    import sqlite3

    from plugins.outcomes.store import OutcomesStore

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE turn_outcomes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, "
        "turn TEXT NOT NULL, turn_score REAL NOT NULL, ts REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO turn_outcomes (session_id, turn, turn_score, ts) VALUES ('S','t',0.5,1.0)"
    )
    conn.commit()
    conn.close()

    # Suppress the additive migration so the column genuinely stays absent.
    monkeypatch.setattr(OutcomesStore, "_init_schema", lambda self: None)
    store = OutcomesStore(db)
    assert store.record_user_rating(session_id="S", turn="t", signal=1.0) is False

    # The legacy row is untouched (no user_rating column to write).
    raw = sqlite3.connect(str(db))
    cols = {r[1] for r in raw.execute("PRAGMA table_info(turn_outcomes)")}
    raw.close()
    assert "user_rating" not in cols
