"""Integration: MemoryStore mutations are snapshotted by the versioning substrate."""

from __future__ import annotations

import tools.memory_tool as memory_tool
from agent.memory_versioning import MemoryVersionLog


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    return memory_tool.MemoryStore(memory_char_limit=2000, user_char_limit=2000)


def _versions(tmp_path, file_name):
    return MemoryVersionLog(tmp_path / ".versions").list_versions(file_name)


def test_add_records_a_version(tmp_path, monkeypatch) -> None:
    s = _store(tmp_path, monkeypatch)
    s.add("memory", "Prefers TypeScript over JavaScript.")
    versions = _versions(tmp_path, "MEMORY.md")
    assert len(versions) >= 1
    assert versions[0]["actor"] == "memory_tool"


def test_each_mutation_adds_a_version(tmp_path, monkeypatch) -> None:
    s = _store(tmp_path, monkeypatch)
    s.add("memory", "Fact one.")
    s.add("memory", "Fact two.")
    versions = _versions(tmp_path, "MEMORY.md")
    # Two mutations → at least two versions, newest carrying both facts.
    assert len(versions) >= 2
    newest = MemoryVersionLog(tmp_path / ".versions").restore(versions[0]["memver"])
    assert b"Fact two." in newest


def test_version_enables_point_in_time_recovery(tmp_path, monkeypatch) -> None:
    s = _store(tmp_path, monkeypatch)
    s.add("memory", "Original fact.")
    v_after_first = _versions(tmp_path, "MEMORY.md")[0]["memver"]
    s.add("memory", "Second fact.")
    log = MemoryVersionLog(tmp_path / ".versions")
    # The first version still recoverable, without the second fact.
    restored = log.restore(v_after_first)
    assert b"Original fact." in restored
    assert b"Second fact." not in restored


def test_versioning_failure_never_breaks_a_memory_write(tmp_path, monkeypatch) -> None:
    s = _store(tmp_path, monkeypatch)
    # Force the version recorder to blow up; the memory write must still succeed.
    monkeypatch.setattr(
        "tools.memory_tool._record_memory_version",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    res = s.add("memory", "Resilient fact.")
    assert res.get("success") is True
