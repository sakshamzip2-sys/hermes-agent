"""Unit tests for the dream_orchestrator plugin (Phase 1: orchestrate + report).

Covers: the sqlite ledger + global lock (idempotency / no concurrent runs), the
DreamTarget adapter contract, target health/skip behaviour, and run_all/plan/status
rendering. All network/in-process dreamers are stubbed — no live services.
"""

from __future__ import annotations

import pytest

from plugins.dream_orchestrator import (
    register,
    render_run,
    render_status,
    run_all,
    status,
)
from plugins.dream_orchestrator.config import OrchestratorConfig, load_orchestrator_config
from plugins.dream_orchestrator.store import OrchestratorStore
from plugins.dream_orchestrator.targets import (
    DreamTarget,
    TargetResult,
    build_targets,
)


# ---------------------------------------------------------------------------
# Store: lock + run ledger + import ledger
# ---------------------------------------------------------------------------
def test_store_lock_prevents_concurrent_runs(tmp_path):
    st = OrchestratorStore(tmp_path / "orch.db")
    assert st.acquire_lock("run-a") is True
    # A second run can't take the lock while the first holds it.
    assert st.acquire_lock("run-b") is False
    st.release_lock("run-a")
    # After release, a new run can take it.
    assert st.acquire_lock("run-b") is True


def test_store_release_is_owner_scoped(tmp_path):
    st = OrchestratorStore(tmp_path / "orch.db")
    st.acquire_lock("run-a")
    # A non-owner release must NOT free the lock.
    st.release_lock("run-b")
    assert st.acquire_lock("run-c") is False


def test_store_records_and_reads_runs(tmp_path):
    st = OrchestratorStore(tmp_path / "orch.db")
    st.record_run("dr-1", {"targets": []}, started_at=100.0, finished_at=101.0)
    last = st.last_run()
    assert last is not None
    assert last["dream_run_id"] == "dr-1"
    assert last["summary"] == {"targets": []}


def test_store_import_ledger_idempotent(tmp_path):
    st = OrchestratorStore(tmp_path / "orch.db")
    assert st.is_imported("x1") is False
    st.mark_imported("x1", source="honcho", ref="c1")
    st.mark_imported("x1", source="honcho", ref="c1")  # idempotent
    assert st.is_imported("x1") is True
    assert "x1" in st.imported_ids()


# ---------------------------------------------------------------------------
# Targets: adapter contract + ordering
# ---------------------------------------------------------------------------
def test_build_targets_respects_toggles_and_order():
    targets = build_targets({"local": True, "honcho": True, "gbrain": True})
    names = [t.name for t in targets]
    # Topology order: honcho -> gbrain -> local (upstream first).
    assert names == ["honcho", "gbrain", "local"]

    only_local = build_targets({"local": True, "honcho": False, "gbrain": False})
    assert [t.name for t in only_local] == ["local"]


class _FakeTarget(DreamTarget):
    def __init__(self, name, healthy, result_status="ok"):
        self.name = name
        self._healthy = healthy
        self._result_status = result_status

    def health(self):
        return (self._healthy, f"{self.name} health detail")

    def trigger(self, *, force=False):
        return TargetResult(self.name, self._result_status, f"{self.name} triggered",
                            data={"force": force})


def _cfg(**over) -> OrchestratorConfig:
    cfg = load_orchestrator_config({})
    cfg.cross_feed.enabled = over.get("cross_feed_enabled", False)
    return cfg


# ---------------------------------------------------------------------------
# run_all: orchestration, skip-on-unhealthy, plan, lock-out
# ---------------------------------------------------------------------------
@pytest.fixture()
def patched(monkeypatch, tmp_path):
    """Patch the store path + target builder to use fakes + a temp DB."""
    import plugins.dream_orchestrator as pkg

    store = OrchestratorStore(tmp_path / "orch.db")
    monkeypatch.setattr(pkg, "_store", lambda: store)
    return {"store": store, "monkeypatch": monkeypatch, "pkg": pkg}


def test_run_all_triggers_healthy_skips_unhealthy(patched, monkeypatch):
    fakes = [
        _FakeTarget("honcho", healthy=True),
        _FakeTarget("gbrain", healthy=False),   # down -> skipped, not fatal
        _FakeTarget("local", healthy=True),
    ]
    monkeypatch.setattr("plugins.dream_orchestrator.build_targets", lambda toggles: fakes)
    summary = run_all(force=True, config=_cfg())
    by_name = {t["name"]: t for t in summary["targets"]}
    assert by_name["honcho"]["status"] == "ok"
    assert by_name["gbrain"]["status"] == "skipped"   # health failed -> clean skip
    assert by_name["local"]["status"] == "ok"
    assert summary["locked_out"] is False
    # A ledger row was written.
    assert patched["store"].last_run()["dream_run_id"] == summary["dream_run_id"]


def test_run_all_plan_is_dry_run(patched, monkeypatch):
    fakes = [_FakeTarget("honcho", healthy=True), _FakeTarget("local", healthy=False)]
    triggered = []

    class _Spy(_FakeTarget):
        def trigger(self, *, force=False):
            triggered.append(self.name)
            return super().trigger(force=force)

    spies = [_Spy("honcho", True), _Spy("local", False)]
    monkeypatch.setattr("plugins.dream_orchestrator.build_targets", lambda toggles: spies)
    summary = run_all(plan=True, config=_cfg())
    assert summary["plan"] is True
    # Plan never triggers anything and never writes a ledger row.
    assert triggered == []
    assert patched["store"].last_run() is None
    statuses = {t["name"]: t["status"] for t in summary["targets"]}
    assert statuses == {"honcho": "would_run", "local": "would_skip"}


def test_run_all_locked_out_when_lock_held(patched, monkeypatch):
    # Pre-acquire the lock so the run sees it held.
    patched["store"].acquire_lock("someone-else")
    fakes = [_FakeTarget("local", healthy=True)]
    monkeypatch.setattr("plugins.dream_orchestrator.build_targets", lambda toggles: fakes)
    summary = run_all(force=True, config=_cfg())
    assert summary["locked_out"] is True


def test_render_run_and_status_are_strings(patched, monkeypatch):
    fakes = [_FakeTarget("honcho", healthy=True), _FakeTarget("local", healthy=True)]
    monkeypatch.setattr("plugins.dream_orchestrator.build_targets", lambda toggles: fakes)
    out = render_run(run_all(force=True, config=_cfg()))
    assert "honcho" in out and "local" in out
    st = status()
    assert isinstance(render_status(st), str)


# ---------------------------------------------------------------------------
# register(): plugin registers a slash + CLI command via the ctx facade
# ---------------------------------------------------------------------------
def test_register_wires_commands():
    calls = {"slash": [], "cli": []}

    class _Ctx:
        def register_command(self, name, handler, description="", args_hint=""):
            calls["slash"].append(name)

        def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
            calls["cli"].append(name)

    register(_Ctx())
    assert "dream-all" in calls["slash"]
    assert "dream-all" in calls["cli"]


def test_config_defaults_are_conservative():
    cfg = load_orchestrator_config({})
    assert cfg.enabled is False
    assert cfg.schedule == ""
    assert cfg.cross_feed.dry_run is True
    assert cfg.cross_feed.enabled is False
