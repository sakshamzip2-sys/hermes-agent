"""The extraction digest must drop tool/markdown/narration noise, keep real facts.

The local dreamer used to feed RAW agentic output (tool-call JSON, tool narration,
markdown table fragments) to the fact extractor, producing ~33% junk candidates.
``candidates._format_digest`` now filters those out BEFORE extraction. These tests
prove noise lines are excluded and that real user facts (and tool narration that
happens to also carry a fact) are NOT lost over-aggressively.
"""

from __future__ import annotations

from plugins.dreaming import candidates as candmod


def _digest(turns: list[tuple[str, str]]) -> str:
    rows = [{"role": r, "content": c} for r, c in turns]
    return candmod._format_digest(rows)


def test_drops_tool_call_json_lines():
    out = _digest([
        ("assistant", '{"name": "search_files", "arguments": {"query": "foo"}}'),
        ("user", "I prefer dark mode in all my editors"),
    ])
    assert "search_files" not in out
    assert "I prefer dark mode" in out


def test_drops_tool_result_and_jsonrpc_fragments():
    out = _digest([
        ("assistant", '"tool_result": {"ok": true, "rows": 12}'),
        ("assistant", '{"tool_call_id": "abc", "content": "..."}'),
        ("user", "My timezone is Asia/Kolkata"),
    ])
    assert "tool_result" not in out
    assert "tool_call_id" not in out
    assert "Asia/Kolkata" in out


def test_drops_markdown_table_rows_and_separators():
    content = (
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| foo | 1 |\n"
        "Here is the real point: I work mostly in Python."
    )
    out = _digest([("assistant", content)])
    assert "| Name | Value |" not in out
    assert "| --- | --- |" not in out
    assert "I work mostly in Python" in out


def test_drops_tool_narration_lines():
    out = _digest([
        ("assistant", "Now let me look at the configuration file to check the value."),
        ("assistant", "I'll run the test suite to verify."),
        ("user", "I always deploy on Fridays"),
    ])
    assert "let me look at" not in out.lower()
    assert "run the test suite" not in out.lower()
    assert "I always deploy on Fridays" in out


def test_keeps_real_user_facts_conservatively():
    # Conversational prose that merely mentions tools must NOT be dropped — only
    # genuine narration/JSON/table noise is filtered.
    out = _digest([
        ("user", "I use the search feature in my IDE constantly."),
        ("user", "Let me know when the report is ready."),  # not a tool-use narration
        ("assistant", "Your monthly budget is 5000 dollars."),
    ])
    assert "search feature in my IDE" in out
    assert "Let me know when the report is ready" in out
    assert "monthly budget is 5000" in out


def test_turn_that_is_all_noise_is_skipped_entirely():
    out = _digest([
        ("assistant", '{"name": "terminal", "arguments": {"cmd": "ls"}}'),
        ("assistant", "| a | b |\n| - | - |"),
    ])
    assert out == ""


def test_multiline_turn_keeps_fact_lines_only():
    content = (
        "Now let me check the database schema.\n"
        '{"name": "database", "arguments": {}}\n'
        "The user runs a SaaS company called Acme."
    )
    out = _digest([("assistant", content)])
    assert "check the database schema" not in out.lower()
    assert "database" not in out or "SaaS" in out  # the JSON line is gone
    assert "SaaS company called Acme" in out
