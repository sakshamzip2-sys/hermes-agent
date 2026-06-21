"""GAP4: hermes orchestrator CLI (run/status) + plugin registration.

Proves the orchestrator has a real user entry point. run --no-assign plans
deterministically (no model needed: brain falls back); status reads the ledger;
and register() wires both the CLI subcommand and the slash command.
"""

from __future__ import annotations

import argparse
import types

import pytest

from plugins import oc_orchestrator
from plugins.oc_orchestrator import cli
from plugins.oc_orchestrator import db as odb


def _parse(argv):
    p = argparse.ArgumentParser()
    cli.setup(p)
    return p.parse_args(argv)


def test_run_plan_only_is_deterministic(capsys, monkeypatch):
    # Force no model so we exercise the deterministic fallback path.
    monkeypatch.setattr(cli, "brain", _no_model_brain(), raising=False)
    args = _parse(["run", "Fix the failing unit test in the parser", "--no-assign"])
    rc = cli.handle(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "shape: single" in out and "coder" in out
    assert "no Kanban cards created" in out


def test_run_swarm_plan_only(capsys, monkeypatch):
    monkeypatch.setattr(cli, "brain", _no_model_brain(), raising=False)
    args = _parse(["run", "Research the market then build a financial model", "--no-assign"])
    rc = cli.handle(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "shape: swarm" in out


def _no_model_brain():
    # A brain module stand-in whose gateway_llm raises (forces deterministic
    # fallback) but whose route_decompose is the real one.
    from plugins.oc_orchestrator import brain as real_brain

    def _raise(*a, **k):
        raise RuntimeError("no model in test")

    return types.SimpleNamespace(
        gateway_llm=_raise,
        route_decompose=real_brain.route_decompose,
    )


def test_status_reads_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_OC_ORCHESTRATOR_DB", str(tmp_path / "orch.db"))
    for attr in ("conn", "path"):
        if hasattr(odb._local, attr):
            delattr(odb._local, attr)
    from plugins.oc_orchestrator import caps
    with odb.connect() as conn:
        caps.ensure_tree(conn, "t1")
        caps.spawn_guarded(conn, "t1", depth=1)
    rc = cli.handle(_parse(["status"]))
    out = capsys.readouterr().out
    assert rc == 0
    assert "slot reservations" in out and "t1" in out


def test_register_wires_cli_and_slash():
    registered = {"cli": [], "slash": []}
    ctx = types.SimpleNamespace(
        register_cli_command=lambda name, **k: registered["cli"].append(name),
        register_command=lambda name, *a, **k: registered["slash"].append(name),
    )
    oc_orchestrator.register(ctx)
    assert "orchestrator" in registered["cli"]
    assert "orchestrator" in registered["slash"]
