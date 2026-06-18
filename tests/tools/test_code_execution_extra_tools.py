#!/usr/bin/env python3
"""Tests for code_execution.extra_tools — widening the PTC sandbox allowlist.

The execute_code sandbox ships with a 7-tool allowlist (SANDBOX_ALLOWED_TOOLS).
``code_execution.extra_tools`` (config.yaml) lets an operator opt extra tools
into the sandbox so multi-step pipelines (e.g. browser research) collapse into a
single execute_code turn instead of N tool round-trips.

Invariants under test:
  1. No config  -> allowlist stays the base 7 (no behavior change by default).
  2. extra_tools EXPANDS the ceiling (base 7 still present).
  3. An extra tool with no hardcoded _TOOL_STUBS entry still gets a *generic*
     callable stub (otherwise widening silently no-ops).
  4. SECURITY: extra_tools is always intersected with the session's actual
     tools — a tool the session does not have never gets a stub.
  5. Non-identifier tool names are skipped (cannot be a Python import) without
     crashing.
  6. The schema description advertises extra tools that are enabled.

Run with:  python -m pytest tests/tools/test_code_execution_extra_tools.py -v
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault("TERMINAL_ENV", "local")

from tools.code_execution_tool import (
    SANDBOX_ALLOWED_TOOLS,
    generate_hermes_tools_module,
    build_execute_code_schema,
    execute_code,
)

_CFG = "tools.code_execution_tool._load_config"


def _mock_dispatch(function_name, function_args, task_id=None, user_task=None):
    """Mock RPC dispatcher: canned responses for base + an extra tool."""
    if function_name == "browser_navigate":
        return json.dumps({"title": "Example", "url": function_args.get("url", "")})
    if function_name == "terminal":
        return json.dumps({"output": f"ran: {function_args.get('command','')}", "exit_code": 0})
    return json.dumps({"error": f"unexpected tool {function_name}"})


class TestEffectiveAllowlist(unittest.TestCase):
    def test_no_extra_tools_keeps_base_seven(self):
        from tools.code_execution_tool import effective_sandbox_allowlist
        with patch(_CFG, return_value={}):
            self.assertEqual(effective_sandbox_allowlist(), SANDBOX_ALLOWED_TOOLS)

    def test_extra_tools_expand_allowlist(self):
        from tools.code_execution_tool import effective_sandbox_allowlist
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate", "browser_click"]}):
            eff = effective_sandbox_allowlist()
            self.assertIn("browser_navigate", eff)
            self.assertIn("browser_click", eff)
            self.assertTrue(SANDBOX_ALLOWED_TOOLS.issubset(eff))

    def test_malformed_extra_tools_ignored(self):
        from tools.code_execution_tool import effective_sandbox_allowlist
        # not a list -> ignored, falls back to base 7
        with patch(_CFG, return_value={"extra_tools": "browser_navigate"}):
            self.assertEqual(effective_sandbox_allowlist(), SANDBOX_ALLOWED_TOOLS)


class TestExtraToolStubGeneration(unittest.TestCase):
    def test_extra_tool_gets_generic_stub(self):
        # browser_navigate has no hardcoded _TOOL_STUBS entry. With it allowed
        # AND present in the session, a generic stub must be generated.
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate"]}):
            src = generate_hermes_tools_module(["browser_navigate", "terminal"])
        self.assertIn("def browser_navigate(", src)
        self.assertIn("_call('browser_navigate'", src)
        # base hardcoded stub still generated alongside
        self.assertIn("def terminal(", src)

    def test_extra_tool_not_in_session_is_excluded(self):
        # SECURITY: extra_tools widens the ceiling, but a tool the session does
        # not actually have must NOT get a stub.
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate"]}):
            src = generate_hermes_tools_module(["terminal"])  # browser not in session
        self.assertNotIn("def browser_navigate(", src)

    def test_non_identifier_extra_tool_skipped(self):
        # Names that aren't valid Python identifiers can't be imported — skip
        # them quietly rather than emitting broken source.
        with patch(_CFG, return_value={"extra_tools": ["bad-name", "has space"]}):
            src = generate_hermes_tools_module(["bad-name", "has space", "terminal"])
        self.assertNotIn("bad-name", src)
        self.assertNotIn("has space", src)
        self.assertIn("def terminal(", src)  # valid base tool unaffected

    def test_generated_module_is_valid_python(self):
        # The generic stub must compile (no syntax errors).
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate"]}):
            src = generate_hermes_tools_module(["browser_navigate", "terminal"])
        compile(src, "hermes_tools.py", "exec")  # raises SyntaxError on failure


class TestSchemaAdvertisesExtraTools(unittest.TestCase):
    def test_schema_description_lists_enabled_extra_tool(self):
        from tools.code_execution_tool import effective_sandbox_allowlist
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate"]}):
            enabled = set(effective_sandbox_allowlist() & {"browser_navigate", "terminal"})
            schema = build_execute_code_schema(enabled)
        self.assertIn("browser_navigate", schema["description"])

    def test_schema_default_unchanged_without_extra_tools(self):
        with patch(_CFG, return_value={}):
            schema = build_execute_code_schema()
        # still a valid execute_code schema, base tools present
        self.assertEqual(schema["name"], "execute_code")
        self.assertIn("terminal", schema["description"])
        self.assertNotIn("browser_navigate", schema["description"])


@unittest.skipIf(sys.platform == "win32", "UDS not available on Windows")
class TestExtraToolEndToEndDispatch(unittest.TestCase):
    """Prove the FULL chain: config -> allowlist -> generic stub -> RPC dispatch
    gate -> handle_function_call. This is the part that would be a silent
    half-fix if the runtime dispatch gate still denied the extra tool."""

    def test_extra_tool_round_trips_through_rpc(self):
        code = (
            "from hermes_tools import browser_navigate\n"
            "r = browser_navigate(url='https://example.com')\n"
            "print(r.get('title', ''))\n"
        )
        with patch(_CFG, return_value={"extra_tools": ["browser_navigate"]}), \
             patch("model_tools.handle_function_call", side_effect=_mock_dispatch):
            result = json.loads(execute_code(
                code=code,
                task_id="extra-tools-e2e",
                enabled_tools=["browser_navigate", "terminal"],
            ))
        self.assertEqual(result["status"], "success", result)
        self.assertIn("Example", result["output"])
        self.assertEqual(result["tool_calls_made"], 1)

    def test_dispatch_gate_still_denies_unallowed_tool(self):
        # SECURITY: with NO extra_tools config, a script that tries to RPC a
        # non-allowlisted tool must be denied at the dispatch gate (never
        # dispatched to handle_function_call). The gate returns an error result
        # ("not available in execute_code") rather than executing the tool.
        code = (
            "import hermes_tools\n"
            "print(hermes_tools._call('browser_navigate', {'url': 'x'}))\n"
        )
        with patch(_CFG, return_value={}), \
             patch("model_tools.handle_function_call", side_effect=_mock_dispatch) as disp:
            result = json.loads(execute_code(
                code=code,
                task_id="extra-tools-deny",
                enabled_tools=["terminal"],
            ))
        self.assertEqual(result["status"], "success", result)
        self.assertIn("not available in execute_code", result["output"])
        # The gate must block BEFORE dispatch — browser_navigate never reaches it.
        self.assertNotIn(
            "browser_navigate",
            [c.args[0] for c in disp.call_args_list],
        )


if __name__ == "__main__":
    unittest.main()
