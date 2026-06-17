"""Tests for the dreaming review/rollback queue (HMAC-chained, tamper-evident).

Ported from OpenComputer v1's dreaming_review.py. The chain is the security-critical
piece: any tampering with a queued promotion or rollback row must break verify_chain.
"""

from __future__ import annotations

from plugins.dreaming import review


def test_queue_and_verify_chain(tmp_path) -> None:
    review.queue_pending(
        tmp_path, text="Prefers TS.", source_event_id="e1",
        score=0.8, recall_count=3, diversity_score=0.2, now_ns=1,
    )
    review.queue_pending(
        tmp_path, text="Uses zsh.", source_event_id="e2",
        score=0.7, recall_count=2, diversity_score=0.3, now_ns=2,
    )
    state = review.load_state(tmp_path)
    assert len(state.items) == 2
    assert review.verify_chain(tmp_path) is True


def test_tampering_breaks_the_chain(tmp_path) -> None:
    review.queue_pending(
        tmp_path, text="Original.", source_event_id="e1",
        score=0.8, recall_count=3, diversity_score=0.2, now_ns=1,
    )
    # Tamper: rewrite the item's text on disk without re-HMACing.
    state = review.load_state(tmp_path)
    object.__setattr__(state.items[0], "text", "TAMPERED")
    review.save_state(tmp_path, state)
    assert review.verify_chain(tmp_path) is False


def test_remove_pending(tmp_path) -> None:
    p = review.queue_pending(
        tmp_path, text="x", source_event_id="e1",
        score=0.8, recall_count=3, diversity_score=0.2, now_ns=1,
    )
    removed = review.remove_pending(tmp_path, promotion_id=p.id)
    assert removed is not None and removed.id == p.id
    assert review.load_state(tmp_path).items == []


def test_rollback_recorded_and_chain_holds(tmp_path) -> None:
    review.queue_pending(
        tmp_path, text="x", source_event_id="e1",
        score=0.8, recall_count=3, diversity_score=0.2, now_ns=1,
    )
    review.record_rollback(tmp_path, memory_id="m1", reverted_text="x", now_ns=2)
    assert review.verify_chain(tmp_path) is True
    assert len(review.load_state(tmp_path).rollback_log) == 1


def test_supersede_entry_carries_old_text(tmp_path) -> None:
    p = review.queue_pending(
        tmp_path, text="new", source_event_id="e1",
        score=0.8, recall_count=3, diversity_score=0.2, old_text="stale", now_ns=1,
    )
    assert p.old_text == "stale"
    assert review.verify_chain(tmp_path) is True


def test_strip_revoked_lines() -> None:
    body = "fact one\n# REVOKED 2026-06-17 m1: secret\nfact two"
    out = review.strip_revoked_lines(body)
    assert "REVOKED" not in out
    assert "fact one" in out and "fact two" in out


def test_format_revoked_marker_is_single_line() -> None:
    marker = review.format_revoked_marker(memory_id="m1", reverted_text="multi\nline\ntext", ts_ns=1_000_000_000)
    assert marker.count("\n") <= 2  # leading/trailing newline only
    assert "REVOKED" in marker and "m1" in marker


def test_empty_state_verifies(tmp_path) -> None:
    assert review.verify_chain(tmp_path) is True
    assert review.load_state(tmp_path).items == []
