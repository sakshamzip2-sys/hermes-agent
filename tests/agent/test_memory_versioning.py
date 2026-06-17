"""Tests for the immutable memory-versioning substrate (Anthropic memory-stores model).

Every write snapshots an immutable version; versions are listable, restorable
(point-in-time recovery), and individually redactable (scrub content, keep the audit row).
"""

from __future__ import annotations

from agent.memory_versioning import MemoryVersionLog


def _log(tmp_path):
    return MemoryVersionLog(tmp_path / ".versions")


def test_record_returns_memver_and_lists_it(tmp_path) -> None:
    log = _log(tmp_path)
    mv = log.record_version("MEMORY.md", b"fact one", op="promote", actor="dreaming", ts=1.0)
    assert isinstance(mv, str) and mv.startswith("memver_")
    rows = log.list_versions("MEMORY.md")
    assert len(rows) == 1
    assert rows[0]["memver"] == mv
    assert rows[0]["op"] == "promote"
    assert rows[0]["actor"] == "dreaming"


def test_restore_returns_exact_snapshot(tmp_path) -> None:
    log = _log(tmp_path)
    mv1 = log.record_version("MEMORY.md", b"v1 content", op="write", actor="user", ts=1.0)
    log.record_version("MEMORY.md", b"v2 content", op="write", actor="user", ts=2.0)
    # Point-in-time recovery of the FIRST version.
    assert log.restore(mv1) == b"v1 content"


def test_versions_are_newest_first_and_chained(tmp_path) -> None:
    log = _log(tmp_path)
    mv1 = log.record_version("MEMORY.md", b"a", op="write", actor="u", ts=1.0)
    mv2 = log.record_version("MEMORY.md", b"b", op="write", actor="u", ts=2.0)
    rows = log.list_versions("MEMORY.md")
    assert [r["memver"] for r in rows] == [mv2, mv1]  # newest first
    assert rows[0]["prev_memver"] == mv1  # chained to predecessor
    assert rows[1]["prev_memver"] is None  # genesis


def test_files_are_isolated(tmp_path) -> None:
    log = _log(tmp_path)
    log.record_version("MEMORY.md", b"m", op="write", actor="u", ts=1.0)
    log.record_version("USER.md", b"u", op="write", actor="u", ts=2.0)
    assert len(log.list_versions("MEMORY.md")) == 1
    assert len(log.list_versions("USER.md")) == 1


def test_redact_scrubs_content_but_keeps_audit_row(tmp_path) -> None:
    log = _log(tmp_path)
    mv = log.record_version("MEMORY.md", b"my secret token is sk-abc123", op="write", actor="u", ts=1.0)
    changed = log.redact(mv, r"sk-[a-z0-9]+")
    assert changed is True
    # Content scrubbed...
    assert b"sk-abc123" not in log.restore(mv)
    assert b"REDACTED" in log.restore(mv)
    # ...but the audit row still exists (tamper-evident trail preserved).
    rows = log.list_versions("MEMORY.md")
    assert len(rows) == 1
    assert rows[0]["memver"] == mv
    assert rows[0].get("redacted") is True


def test_restore_unknown_memver_returns_none(tmp_path) -> None:
    log = _log(tmp_path)
    assert log.restore("memver_does_not_exist") is None


def test_persists_across_instances(tmp_path) -> None:
    root = tmp_path / ".versions"
    mv = MemoryVersionLog(root).record_version("MEMORY.md", b"x", op="write", actor="u", ts=1.0)
    # Fresh instance reads the prior index + snapshot.
    assert MemoryVersionLog(root).restore(mv) == b"x"


def test_soft_cap_warning_flag(tmp_path) -> None:
    log = _log(tmp_path)
    big = b"x" * (101 * 1024)  # > 100kB soft cap
    mv = log.record_version("MEMORY.md", big, op="write", actor="u", ts=1.0)
    rows = log.list_versions("MEMORY.md")
    assert rows[0]["memver"] == mv
    assert rows[0].get("over_soft_cap") is True
