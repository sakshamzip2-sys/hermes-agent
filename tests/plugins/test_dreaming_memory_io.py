"""Tests for dreaming MEMORY.md / DREAMS.md I/O against a temp memories dir."""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.dreaming import memory_io


@pytest.fixture()
def mem_dir(tmp_path, monkeypatch):
    d = tmp_path / "memories"
    d.mkdir(parents=True)
    monkeypatch.setattr(memory_io, "get_memory_dir", lambda: d)
    return d


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_promote_appends_dated_entry(mem_dir):
    memory_io.promote("Lives in Berlin.")
    content = _read(mem_dir / "MEMORY.md")
    assert "Lives in Berlin." in content
    assert content.startswith("(dreamed ")


def test_promote_uses_section_delimiter(mem_dir):
    memory_io.promote("Fact one.")
    memory_io.promote("Fact two.")
    entries = memory_io.read_memory_entries()
    assert len(entries) == 2
    assert any("Fact one." in e for e in entries)
    assert any("Fact two." in e for e in entries)


def test_promote_dedup_skips_existing_fact(mem_dir):
    memory_io.promote("Same fact.")
    memory_io.promote("Same fact.")
    assert len(memory_io.read_memory_entries()) == 1


def test_promote_empty_is_noop(mem_dir):
    memory_io.promote("   ")
    assert memory_io.read_memory_entries() == []


def test_replace_in_place(mem_dir):
    memory_io.promote("Uses npm.")
    ok = memory_io.replace("Uses npm.", "Uses pnpm now.")
    assert ok is True
    entries = memory_io.read_memory_entries()
    assert len(entries) == 1
    assert "pnpm" in entries[0]
    assert "Uses npm." not in entries[0]


def test_replace_missing_returns_false(mem_dir):
    memory_io.promote("Something.")
    assert memory_io.replace("does-not-exist", "new") is False
    assert len(memory_io.read_memory_entries()) == 1


def test_hold_appends_to_dreams(mem_dir):
    memory_io.hold("a held thought", 16384)
    entries = memory_io.read_dreams_entries()
    assert len(entries) == 1
    assert "a held thought" in entries[0]


def test_hold_fifo_evicts_when_over_cap(mem_dir):
    # tiny cap forces eviction of older entries
    for i in range(10):
        memory_io.hold(f"thought number {i} with some padding text", 200)
    entries = memory_io.read_dreams_entries()
    joined = "\n§\n".join(entries)
    assert len(joined.encode("utf-8")) <= 200 or len(entries) == 1
    # newest must survive
    assert any("9" in e for e in entries)


def test_read_dreams_facts_strips_date_prefix(mem_dir):
    memory_io.hold("a durable held thought", 16384)
    facts = memory_io.read_dreams_facts()
    assert facts == ["a durable held thought"]  # no "- DATE: " prefix


def test_write_dreams_facts_roundtrip(mem_dir):
    memory_io.write_dreams_facts(["fact a", "fact b"], 16384)
    assert sorted(memory_io.read_dreams_facts()) == ["fact a", "fact b"]


def test_write_dreams_facts_empty_clears(mem_dir):
    memory_io.write_dreams_facts(["x"], 16384)
    memory_io.write_dreams_facts([], 16384)
    assert memory_io.read_dreams_facts() == []


def test_read_memory_facts_strips_dreamed_marker(mem_dir):
    memory_io.promote("Lives in Berlin.")
    raw = memory_io.read_memory_entries()
    facts = memory_io.read_memory_facts()
    assert raw[0].startswith("(dreamed ")
    assert facts == ["Lives in Berlin."]  # marker stripped for diversity corpus
