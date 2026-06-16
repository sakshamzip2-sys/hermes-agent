"""Tests for _tool_result_to_text in the API server adapter.

The structured tool_complete callback hands the streaming layer whatever the
tool returned. _tool_result_to_text collapses that into a capped string so the
chat-completions SSE can carry it to the frontend timeline's Response box.
"""

import json

from gateway.platforms.api_server import (
    _TOOL_RESULT_STREAM_LIMIT,
    _tool_result_to_text,
)


class TestToolResultToText:
    def test_none_returns_empty_string(self):
        # None/empty must render no Response box, not the literal "None".
        assert _tool_result_to_text(None) == ""

    def test_plain_string_returned_as_is(self):
        assert _tool_result_to_text("delegated report text") == "delegated report text"

    def test_empty_string_returned_as_is(self):
        assert _tool_result_to_text("") == ""

    def test_dict_is_json_encoded(self):
        out = _tool_result_to_text({"status": "ok", "n": 3})
        assert json.loads(out) == {"status": "ok", "n": 3}

    def test_list_is_json_encoded(self):
        out = _tool_result_to_text([1, "a", {"b": 2}])
        assert json.loads(out) == [1, "a", {"b": 2}]

    def test_non_serializable_falls_back_to_str(self):
        class Weird:
            def __repr__(self):
                return "<weird>"

        # default=str handles most objects; if json still fails we str() it.
        out = _tool_result_to_text(Weird())
        assert isinstance(out, str) and out

    def test_large_result_is_truncated(self):
        big = "x" * (_TOOL_RESULT_STREAM_LIMIT + 5000)
        out = _tool_result_to_text(big)
        assert len(out) <= _TOOL_RESULT_STREAM_LIMIT + len("\n…[truncated]")
        assert out.endswith("…[truncated]")

    def test_under_limit_not_truncated(self):
        s = "y" * 100
        assert _tool_result_to_text(s) == s

    def test_custom_limit_respected(self):
        out = _tool_result_to_text("abcdef", limit=3)
        assert out == "abc\n…[truncated]"

    def test_multibyte_truncation_no_corruption(self):
        # "あ" is 3 bytes in UTF-8. Five of them = 15 bytes; cap at 10 bytes.
        # The byte cap must NOT leave a half-encoded character (no U+FFFD), and
        # the truncated prefix must stay within the byte budget.
        out = _tool_result_to_text("あ" * 5, limit=10)
        assert out.endswith("…[truncated]")
        prefix = out[: -len("\n…[truncated]")]
        assert prefix == "あああ"  # 9 bytes <= 10; the 4th would split → dropped
        assert "�" not in out
        assert len(prefix.encode("utf-8")) <= 10

    def test_emoji_truncation_no_corruption(self):
        # 4-byte emoji at the boundary must be dropped cleanly, not split.
        out = _tool_result_to_text("😀😀😀", limit=10)  # 12 bytes total
        prefix = out[: -len("\n…[truncated]")]
        assert prefix == "😀😀"  # 8 bytes <= 10; 3rd (would reach 12) dropped
        assert "�" not in out
