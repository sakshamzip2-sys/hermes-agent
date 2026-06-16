"""Tests for the bang (!) shell-passthrough mode in the interactive CLI.

Ports Claude Code's ``!`` interactive shell mode: ``! ls`` runs the command in
the shell and shows output, without the agent interpreting it.  We test the
detector (what counts as a bang command vs a message the agent should see) and
the handler (runs a real command, bounded output, no shell=True).
"""

from __future__ import annotations

import cli as cli_mod


# ── detector ──────────────────────────────────────────────────────────────────


def test_detects_bang_command():
    assert cli_mod._looks_like_bang_command("! ls -la") is True
    assert cli_mod._looks_like_bang_command("!git status") is True
    assert cli_mod._looks_like_bang_command("!  echo hi  ") is True


def test_bare_bang_is_not_a_command():
    # A lone "!" has no command body -> leave it to the agent.
    assert cli_mod._looks_like_bang_command("!") is False
    assert cli_mod._looks_like_bang_command("!   ") is False


def test_non_bang_is_not_a_command():
    assert cli_mod._looks_like_bang_command("ls -la") is False
    assert cli_mod._looks_like_bang_command("/help") is False
    assert cli_mod._looks_like_bang_command("how do I !important in css?") is False
    assert cli_mod._looks_like_bang_command("") is False
    assert cli_mod._looks_like_bang_command(None) is False  # type: ignore[arg-type]


# ── handler (runs a real shell command) ───────────────────────────────────────


class _DummySelf:
    """The handler uses no instance attributes; a bare object suffices."""


def test_handler_runs_command_and_prints_output(capsys):
    cli_mod.HermesCLI._handle_bang_command(_DummySelf(), "! echo hello_from_bang")
    out = capsys.readouterr().out
    assert "hello_from_bang" in out
    # Echoes the command being run.
    assert "echo hello_from_bang" in out


def test_handler_reports_nonzero_exit(capsys):
    cli_mod.HermesCLI._handle_bang_command(_DummySelf(), "! sh -c 'exit 3'")
    out = capsys.readouterr().out
    assert "exit 3" in out


def test_handler_supports_pipes(capsys):
    cli_mod.HermesCLI._handle_bang_command(
        _DummySelf(), "! printf 'a\\nb\\nc\\n' | grep b"
    )
    out = capsys.readouterr().out
    assert "b" in out


def test_handler_empty_body_is_noop(capsys):
    cli_mod.HermesCLI._handle_bang_command(_DummySelf(), "!   ")
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_no_shell_true_in_bang_handler():
    """Bang mode must not use subprocess shell=True (uses [shell, -c, cmd])."""
    import inspect

    src = inspect.getsource(cli_mod.HermesCLI._handle_bang_command)
    assert "shell=True" not in src
