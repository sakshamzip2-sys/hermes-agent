"""Hook TYPES + exit-code-2: porting Claude Code's prompt/agent hooks and the
``exit(2) == block`` convention onto v2's shell-hook engine
(``agent/shell_hooks.py``).

v2 previously supported ONLY ``command`` (shell-subprocess) hooks. Claude Code
also defines:

  * **exit code 2 (+ stderr) = block** — the canonical CC block convention; a
    ported CC hook script that ``sys.exit(2)``s should block in v2 too.
  * **type: prompt** — an LLM judges the event and returns ``{decision, reason}``.
  * **type: agent**  — a tool-enabled sub-agent investigates, then returns the
    same shape.

prompt/agent hooks reuse the SAME stdout-JSON wire contract as command hooks, so
``_parse_response`` normalises all three uniformly. They are model-agnostic: the
model is resolved from the user's config via ``auxiliary_client.call_llm`` /
``oneshot._run_agent`` — never a hardcoded vendor.
"""

from __future__ import annotations

import json

from agent import shell_hooks


# --------------------------------------------------------------------------
# exit code 2 == block (CC convention)
# --------------------------------------------------------------------------
class TestExitCode2Block:
    def test_exit_2_blocks_pre_tool_call_with_stderr_reason(self, tmp_path):
        script = tmp_path / "deny.py"
        script.write_text(
            "import sys; sys.stderr.write('nope: rm is forbidden'); sys.exit(2)\n",
            encoding="utf-8",
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", command=f"python3 {script}"
        )
        cb = shell_hooks._make_callback(spec)
        result = cb(tool_name="terminal", args={"command": "rm -rf /"}, session_id="s")
        assert result == {"action": "block", "message": "nope: rm is forbidden"}

    def test_exit_2_without_stderr_uses_default_message(self, tmp_path):
        script = tmp_path / "deny2.py"
        script.write_text("import sys; sys.exit(2)\n", encoding="utf-8")
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", command=f"python3 {script}"
        )
        cb = shell_hooks._make_callback(spec)
        result = cb(tool_name="terminal", args={}, session_id="s")
        assert result is not None and result["action"] == "block"

    def test_exit_2_json_stdout_still_takes_precedence(self, tmp_path):
        script = tmp_path / "deny3.py"
        script.write_text(
            'import sys; print(\'{"decision":"block","reason":"json reason"}\'); '
            "sys.exit(2)\n",
            encoding="utf-8",
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", command=f"python3 {script}"
        )
        cb = shell_hooks._make_callback(spec)
        result = cb(tool_name="terminal", args={}, session_id="s")
        assert result == {"action": "block", "message": "json reason"}

    def test_exit_2_on_non_blocking_event_is_not_a_block(self, tmp_path):
        # post_tool_call cannot block; exit 2 there must not synthesize a block.
        script = tmp_path / "deny4.py"
        script.write_text(
            "import sys; sys.stderr.write('x'); sys.exit(2)\n", encoding="utf-8"
        )
        spec = shell_hooks.ShellHookSpec(
            event="post_tool_call", command=f"python3 {script}"
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(tool_name="terminal", args={}, session_id="s") is None

    def test_exit_0_with_no_output_is_observer_only(self, tmp_path):
        script = tmp_path / "noop.py"
        script.write_text("import sys; sys.exit(0)\n", encoding="utf-8")
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", command=f"python3 {script}"
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(tool_name="terminal", args={}, session_id="s") is None


# --------------------------------------------------------------------------
# parsing typed entries
# --------------------------------------------------------------------------
class TestParseTypedEntries:
    def test_prompt_type_parsed(self):
        specs = shell_hooks._parse_hooks_block(
            {
                "pre_tool_call": [
                    {
                        "type": "prompt",
                        "prompt": "Is this destructive?",
                        "matcher": "terminal",
                    }
                ]
            }
        )
        assert len(specs) == 1
        assert specs[0].hook_type == "prompt"
        assert specs[0].prompt == "Is this destructive?"
        assert specs[0].matcher == "terminal"

    def test_agent_type_parsed(self):
        specs = shell_hooks._parse_hooks_block(
            {"pre_tool_call": [{"type": "agent", "prompt": "Investigate; block if unsafe."}]}
        )
        assert len(specs) == 1
        assert specs[0].hook_type == "agent"
        assert specs[0].prompt == "Investigate; block if unsafe."

    def test_command_type_is_default(self):
        specs = shell_hooks._parse_hooks_block({"post_tool_call": [{"command": "/bin/true"}]})
        assert specs[0].hook_type == "command"

    def test_prompt_type_without_prompt_field_skipped(self):
        specs = shell_hooks._parse_hooks_block({"pre_tool_call": [{"type": "prompt"}]})
        assert specs == []

    def test_unknown_type_skipped(self):
        specs = shell_hooks._parse_hooks_block(
            {"pre_tool_call": [{"type": "telepathy", "prompt": "x"}]}
        )
        assert specs == []


# --------------------------------------------------------------------------
# prompt hooks (LLM judgment via call_llm)
# --------------------------------------------------------------------------
def _fake_llm_response(content: str):
    class _Msg:
        def __init__(self, c):
            self.content = c

    class _Choice:
        def __init__(self, c):
            self.message = _Msg(c)

    class _Resp:
        def __init__(self, c):
            self.choices = [_Choice(c)]

    return _Resp(content)


class TestPromptHook:
    def test_prompt_hook_blocks_when_llm_says_block(self, monkeypatch):
        captured: dict = {}

        def _fake_call_llm(**kw):
            captured.update(kw)
            return _fake_llm_response('{"decision":"block","reason":"LLM judged destructive"}')

        monkeypatch.setattr("agent.auxiliary_client.call_llm", _fake_call_llm)
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call",
            hook_type="prompt",
            prompt="Block destructive commands.",
            matcher="terminal",
        )
        cb = shell_hooks._make_callback(spec)
        result = cb(tool_name="terminal", args={"command": "rm -rf /"}, session_id="s")
        assert result == {"action": "block", "message": "LLM judged destructive"}
        # The hook's prompt + the event payload must reach the model.
        msgs = captured.get("messages")
        assert msgs is not None
        blob = json.dumps(msgs)
        assert "Block destructive commands." in blob
        assert "rm -rf /" in blob

    def test_prompt_hook_allows_when_llm_says_allow(self, monkeypatch):
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: _fake_llm_response('{"decision":"allow"}'),
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="prompt", prompt="...", matcher="terminal"
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(tool_name="terminal", args={}, session_id="s") is None

    def test_prompt_hook_matcher_gates_before_llm(self, monkeypatch):
        calls = {"n": 0}

        def _fake(**kw):
            calls["n"] += 1
            return _fake_llm_response("{}")

        monkeypatch.setattr("agent.auxiliary_client.call_llm", _fake)
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="prompt", prompt="x", matcher="web_search"
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(tool_name="terminal", args={}, session_id="s") is None
        assert calls["n"] == 0  # matcher excluded the tool → no LLM call

    def test_prompt_hook_llm_failure_is_fail_open(self, monkeypatch):
        def _boom(**kw):
            raise RuntimeError("provider down")

        monkeypatch.setattr("agent.auxiliary_client.call_llm", _boom)
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="prompt", prompt="x"
        )
        cb = shell_hooks._make_callback(spec)
        # An LLM error must neither raise nor block (fail-open, like command hooks).
        assert cb(tool_name="terminal", args={}, session_id="s") is None

    def test_prompt_hook_context_injection_for_pre_llm_call(self, monkeypatch):
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: _fake_llm_response('{"context":"Remember: it is Friday."}'),
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_llm_call", hook_type="prompt", prompt="Inject the day."
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(session_id="s", user_message="hi") == {"context": "Remember: it is Friday."}


# --------------------------------------------------------------------------
# agent hooks (tool-enabled sub-agent via oneshot._run_agent)
# --------------------------------------------------------------------------
class TestAgentHook:
    def test_agent_hook_blocks_via_run_agent(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.oneshot._run_agent",
            lambda *a, **k: {
                "final_response": '{"decision":"block","reason":"agent says no"}',
                "failed": False,
            },
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="agent", prompt="Investigate."
        )
        cb = shell_hooks._make_callback(spec)
        result = cb(tool_name="terminal", args={}, session_id="s")
        assert result == {"action": "block", "message": "agent says no"}

    def test_agent_hook_failure_is_fail_open(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("agent crashed")

        monkeypatch.setattr("hermes_cli.oneshot._run_agent", _boom)
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="agent", prompt="x"
        )
        cb = shell_hooks._make_callback(spec)
        assert cb(tool_name="terminal", args={}, session_id="s") is None


# --------------------------------------------------------------------------
# recursion guard: a prompt/agent hook's own model work must not re-fire hooks
# --------------------------------------------------------------------------
class TestRecursionGuard:
    def test_nested_hook_eval_is_suppressed(self, monkeypatch):
        observed = {"inner_result": "unset"}

        def _fake_call_llm(**kw):
            # Simulate the nested model run itself triggering a pre_tool_call
            # hook. While we are inside a prompt/agent hook eval, that inner
            # firing MUST be a no-op, or an agent hook with tools would recurse
            # forever.
            inner = shell_hooks._make_callback(
                shell_hooks.ShellHookSpec(
                    event="pre_tool_call", hook_type="prompt", prompt="inner"
                )
            )
            observed["inner_result"] = inner(
                tool_name="terminal", args={}, session_id="s"
            )
            return _fake_llm_response('{"decision":"allow"}')

        monkeypatch.setattr("agent.auxiliary_client.call_llm", _fake_call_llm)
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="prompt", prompt="outer"
        )
        cb = shell_hooks._make_callback(spec)
        cb(tool_name="terminal", args={}, session_id="s")
        assert observed["inner_result"] is None


# --------------------------------------------------------------------------
# run_once (oc hooks test / doctor) works for every hook type
# --------------------------------------------------------------------------
class TestRunOnceDispatch:
    def test_run_once_prompt_hook(self, monkeypatch):
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: _fake_llm_response('{"decision":"block","reason":"r"}'),
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="prompt", prompt="x"
        )
        diag = shell_hooks.run_once(
            spec, {"tool_name": "terminal", "args": {"command": "rm"}}
        )
        assert diag["error"] is None
        assert diag["parsed"] == {"action": "block", "message": "r"}

    def test_run_once_agent_hook(self, monkeypatch):
        monkeypatch.setattr(
            "hermes_cli.oneshot._run_agent",
            lambda *a, **k: {"final_response": '{"decision":"allow"}', "failed": False},
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", hook_type="agent", prompt="x"
        )
        diag = shell_hooks.run_once(spec, {"tool_name": "terminal", "args": {}})
        assert diag["error"] is None
        assert diag["parsed"] is None  # allow → no block directive

    def test_run_once_command_hook_still_works(self, tmp_path):
        script = tmp_path / "h.py"
        script.write_text(
            'import sys; print(\'{"decision":"block","reason":"cmd"}\')\n',
            encoding="utf-8",
        )
        spec = shell_hooks.ShellHookSpec(
            event="pre_tool_call", command=f"python3 {script}"
        )
        diag = shell_hooks.run_once(spec, {"tool_name": "terminal", "args": {}})
        assert diag["parsed"] == {"action": "block", "message": "cmd"}
