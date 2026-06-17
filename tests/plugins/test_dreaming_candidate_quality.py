"""Regression: dreaming must not mine assistant answer-structure as 'user facts'.

These exact junk strings were promoted to MEMORY.md in a live run — markdown headers,
bold-label answer fragments, and meta-prose openers from assistant informational replies.
"""

from __future__ import annotations

from plugins.dreaming.candidates import _clean_content, _is_noise_line

# The exact junk that leaked into MEMORY.md (must now be filtered).
JUNK = [
    "## 1. Physical Intelligence (pi / π)",
    "## 1. Apptronik — Apollo",
    "### 2. Figure AI",
    "Here is the structured data for all four companies based on current research.",
    "Here's the breakdown you asked for.",
    "Below is the comparison table.",
    "Bottom line:** Kimi K2.7 is generally better for reasoning and agentic tasks.",
    "Kimi wins** on: reasoning, agentic coding, language",
    "GLM 5.1 wins** on: math, instruction following, plain coding (barely)",
]

# Real durable user facts that MUST survive (precision: never drop these).
REAL_FACTS = [
    "User strongly prefers Rust over Go for backend services.",
    "Prefers Rust for backend systems work.",
    "Plays the guitar.",
    "Favorite fruit is mango.",
    "Lives in Jaipur.",
    "I love working with TypeScript on weekends.",
    "My manager's name is Priya.",
    "We use PostgreSQL for the analytics pipeline.",
]


def test_junk_lines_are_noise() -> None:
    for j in JUNK:
        assert _is_noise_line(j) is True, f"should be noise: {j!r}"


def test_real_user_facts_are_not_noise() -> None:
    for f in REAL_FACTS:
        assert _is_noise_line(f) is False, f"must NOT be dropped: {f!r}"


def test_clean_content_strips_junk_keeps_facts() -> None:
    content = "\n".join([
        "## 1. Physical Intelligence (pi / π)",
        "User strongly prefers Rust over Go for backend services.",
        "Bottom line:** Kimi K2.7 is generally better.",
        "Plays the guitar.",
    ])
    cleaned = _clean_content(content)
    assert "Physical Intelligence" not in cleaned
    assert "Bottom line" not in cleaned
    assert "Rust over Go" in cleaned
    assert "Plays the guitar." in cleaned


def test_markdown_header_levels_all_dropped() -> None:
    for h in ("# Heading", "## Sub", "### Deep", "#### Deeper"):
        assert _is_noise_line(h) is True


def test_bold_used_naturally_in_a_user_fact_survives() -> None:
    # A user emphasising a word with **bold** is NOT answer-structure.
    assert _is_noise_line("I **really** love Rust.") is False
