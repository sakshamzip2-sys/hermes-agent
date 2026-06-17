"""The `dream review` CLI: list / accept / reject / verify against a temp queue."""

from __future__ import annotations

import argparse

from plugins.dreaming import cli, memory_io, review


def _setup(tmp_path, monkeypatch):
    mem = tmp_path / "memories"
    mem.mkdir()
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: mem)
    home = tmp_path / "dreaming"
    monkeypatch.setattr("plugins.dreaming.runner._review_home", lambda: home)
    return mem, home


def _args(**kw):
    ns = argparse.Namespace(review_action="list", promotion_id=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_accept_promotes_to_memory(tmp_path, monkeypatch, capsys):
    mem, home = _setup(tmp_path, monkeypatch)
    p = review.queue_pending(home, text="Prefers dark mode.", source_event_id="e1",
                             score=0.9, recall_count=3, diversity_score=0.1, now_ns=1)
    rc = cli._cmd_review(_args(review_action="accept", promotion_id=p.id))
    assert rc == 0
    # Promoted into MEMORY.md and removed from the queue.
    assert any("Prefers dark mode." in e for e in memory_io.read_memory_entries())
    assert review.load_state(home).items == []


def test_reject_drops_without_promoting(tmp_path, monkeypatch, capsys):
    mem, home = _setup(tmp_path, monkeypatch)
    p = review.queue_pending(home, text="A noisy one-off.", source_event_id="e2",
                             score=0.9, recall_count=3, diversity_score=0.1, now_ns=1)
    rc = cli._cmd_review(_args(review_action="reject", promotion_id=p.id[:8]))
    assert rc == 0
    assert memory_io.read_memory_entries() == []
    assert review.load_state(home).items == []


def test_verify_reports_ok(tmp_path, monkeypatch, capsys):
    mem, home = _setup(tmp_path, monkeypatch)
    review.queue_pending(home, text="x", source_event_id="e", score=0.9,
                         recall_count=3, diversity_score=0.1, now_ns=1)
    rc = cli._cmd_review(_args(review_action="verify"))
    assert rc == 0
    assert "OK" in capsys.readouterr().out
