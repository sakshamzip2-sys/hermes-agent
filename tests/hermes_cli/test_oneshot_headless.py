"""Headless `oc -z` ergonomics: --output-format json, stdin piping, --append-system-prompt.

These port the Claude-Code `claude -p` headless concepts onto the existing
oneshot path (`hermes_cli/oneshot.py`).  The pre-existing behaviour was:
plain final-response text to stdout, exit 0/1/2.  We add:

  * ``--output-format json`` — emit a machine-parseable object even on
    failure/empty, so scripts can ``jq`` the result and read the exit code.
  * stdin piping — ``oc -z -`` reads the prompt from stdin.
  * ``--append-system-prompt`` — appended to the system prompt (context tier)
    via ``run_conversation(system_message=...)``.

All tests mock at the ``_run_agent`` boundary (or ``run_agent.AIAgent``) so no
real model call happens; they assert the wiring, not the LLM.
"""

from __future__ import annotations

import io
import json

from hermes_cli import oneshot


def _stub_run_agent(monkeypatch, *, result=None, capture=None):
    """Replace oneshot._run_agent with a stub returning ``result``.

    If ``capture`` is a dict, the stub records the prompt + kwargs it was
    called with into it.
    """
    def _fake(prompt, **kwargs):
        if capture is not None:
            capture["prompt"] = prompt
            capture.update(kwargs)
        return result if result is not None else {"final_response": "ok"}

    monkeypatch.setattr(oneshot, "_run_agent", _fake)


class TestOneshotJsonOutput:
    def test_json_output_shape_on_success(self, monkeypatch, capsys):
        _stub_run_agent(
            monkeypatch,
            result={
                "final_response": "hello world",
                "session_id": "20260616_000000_abc",
                "failed": False,
                "error": None,
                # run_conversation surfaces token/cost fields FLAT, not nested.
                "input_tokens": 5,
                "output_tokens": 2,
                "total_tokens": 7,
                "estimated_cost_usd": 0.001,
            },
        )
        rc = oneshot.run_oneshot("hi", output_format="json")
        out = capsys.readouterr().out.strip()

        assert rc == 0
        payload = json.loads(out)  # must be valid JSON, single object
        assert payload["final_response"] == "hello world"
        assert payload["session_id"] == "20260616_000000_abc"
        assert payload["failed"] is False
        # usage is projected from the flat token/cost fields into one object.
        assert payload["usage"]["input_tokens"] == 5
        assert payload["usage"]["total_tokens"] == 7
        assert payload["usage"]["estimated_cost_usd"] == 0.001

    def test_json_output_emitted_on_failure_with_nonzero_exit(self, monkeypatch, capsys):
        _stub_run_agent(
            monkeypatch,
            result={
                "final_response": "",
                "session_id": "s1",
                "failed": True,
                "error": "boom",
            },
        )
        rc = oneshot.run_oneshot("hi", output_format="json")
        out = capsys.readouterr().out.strip()

        # JSON is STILL emitted (scripts can parse it) but exit is non-zero.
        assert rc != 0
        payload = json.loads(out)
        assert payload["failed"] is True
        assert payload["error"] == "boom"

    def test_text_output_is_unchanged(self, monkeypatch, capsys):
        # Backward-compat: default text mode prints only the final response.
        _stub_run_agent(
            monkeypatch,
            result={"final_response": "just text", "session_id": "s1", "failed": False},
        )
        rc = oneshot.run_oneshot("hi")  # default output_format="text"
        captured = capsys.readouterr()

        assert rc == 0
        assert captured.out.strip() == "just text"
        # Text mode must NOT emit JSON noise.
        assert "{" not in captured.out


class TestOneshotStdin:
    def test_dash_prompt_reads_stdin(self, monkeypatch):
        capture: dict = {}
        _stub_run_agent(
            monkeypatch,
            result={"final_response": "ack", "failed": False},
            capture=capture,
        )
        monkeypatch.setattr("sys.stdin", io.StringIO("prompt from a pipe\n"))

        rc = oneshot.run_oneshot("-", output_format="text")

        assert rc == 0
        assert capture["prompt"] == "prompt from a pipe"

    def test_empty_stdin_is_an_error(self, monkeypatch, capsys):
        _stub_run_agent(monkeypatch, result={"final_response": "x"})
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))

        rc = oneshot.run_oneshot("-")
        err = capsys.readouterr().err

        assert rc == 2
        assert "stdin" in err.lower() or "empty" in err.lower()


class TestOneshotAppendSystemPrompt:
    def test_append_system_prompt_forwarded_to_run_agent(self, monkeypatch):
        capture: dict = {}
        _stub_run_agent(
            monkeypatch,
            result={"final_response": "ok", "failed": False},
            capture=capture,
        )
        oneshot.run_oneshot("hi", append_system_prompt="EXTRA RULES")
        assert capture.get("append_system_prompt") == "EXTRA RULES"

    def test_run_agent_passes_append_as_system_message(self, monkeypatch):
        """_run_agent must thread append_system_prompt into
        run_conversation(system_message=...) — the seam that appends it to the
        system prompt's context tier (agent/system_prompt.py)."""
        recorded: dict = {}

        class _FakeAgent:
            def __init__(self, *a, **k):
                self.session_id = "sess-xyz"
                self.suppress_status_output = False
                self.stream_delta_callback = None
                self.tool_gen_callback = None

            def run_conversation(self, prompt, system_message=None, **kw):
                recorded["prompt"] = prompt
                recorded["system_message"] = system_message
                return {"final_response": "done", "failed": False}

        monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
        # Keep config/runtime resolution cheap + deterministic: no DB, and a
        # fake provider so we don't need real credentials in the hermetic env.
        monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: None)
        monkeypatch.setattr(
            "hermes_cli.runtime_provider.resolve_runtime_provider",
            lambda **kw: {
                "api_key": "k",
                "base_url": "u",
                "provider": "p",
                "api_mode": "chat_completions",
                "credential_pool": None,
            },
        )

        out = oneshot._run_agent("my prompt", append_system_prompt="APPENDED")
        assert recorded["system_message"] == "APPENDED"
        assert recorded["prompt"] == "my prompt"
        assert isinstance(out, dict)
        assert out["final_response"] == "done"
        # session_id should be surfaced for JSON output.
        assert out.get("session_id") == "sess-xyz"


class TestBuildUsage:
    def test_projects_flat_token_and_cost_fields(self):
        result = {
            "final_response": "x",
            "input_tokens": 10,
            "output_tokens": 4,
            "total_tokens": 14,
            "cache_read_tokens": 2,
            "reasoning_tokens": 1,
            "estimated_cost_usd": 0.002,
            "model": "some-model",
            "provider": "some-provider",
            "irrelevant": "dropped",
        }
        usage = oneshot._build_usage(result)
        assert usage is not None
        assert usage["input_tokens"] == 10
        assert usage["total_tokens"] == 14
        assert usage["estimated_cost_usd"] == 0.002
        assert usage["model"] == "some-model"
        assert "irrelevant" not in usage  # only the usage keys are projected

    def test_none_when_no_usage_data(self):
        # An empty/failed run with no token data → null, not an all-zero object.
        assert oneshot._build_usage({"final_response": ""}) is None
        assert oneshot._build_usage({}) is None
