"""Tests for STEP 4: checkpoints capture and restore the agent's todo/task state.

The existing checkpoint manager snapshots the working tree (shadow git). This
adds optional task_state_provider/task_state_restorer hooks so a checkpoint also
captures the todo list and a rewind restores it. Both default None → file-only
checkpoints exactly as before (verified).
"""

from pathlib import Path

import pytest

from tools.checkpoint_manager import CheckpointManager, _task_state_path, _store_path, _project_hash


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    (d / "main.py").write_text("v1\n")
    return d


@pytest.fixture()
def checkpoint_base(tmp_path, monkeypatch):
    base = tmp_path / "checkpoints"
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", base)
    return base


def _todo_store():
    """A tiny stand-in for the agent's TodoStore: read()/write()."""
    state = {"items": []}

    class _S:
        def read(self):
            return [i.copy() for i in state["items"]]

        def write(self, todos, merge=False):
            state["items"] = [dict(t) for t in (todos or [])]
            return self.read()

    return _S()


def test_checkpoint_captures_and_restores_task_state(work_dir, checkpoint_base):
    store = _todo_store()
    mgr = CheckpointManager(
        enabled=True,
        task_state_provider=lambda: store.read(),
        task_state_restorer=lambda todos: store.write(todos),
    )

    # Initial state: one todo + file v1. Take a checkpoint.
    store.write([{"id": "1", "content": "task A", "status": "in_progress"}])
    assert mgr.ensure_checkpoint(str(work_dir), "snapshot-1") is True

    cps = mgr.list_checkpoints(str(work_dir))
    assert cps, "expected a checkpoint to exist"
    commit = cps[0]["hash"] if "hash" in cps[0] else cps[0].get("commit") or cps[0].get("id")
    assert commit, f"could not find commit hash in checkpoint entry: {cps[0]}"

    # Mutate BOTH the file and the todo list.
    (work_dir / "main.py").write_text("v2-modified\n")
    store.write([{"id": "1", "content": "task A", "status": "completed"},
                 {"id": "2", "content": "task B", "status": "in_progress"}])

    # Restore to the checkpoint.
    result = mgr.restore(str(work_dir), commit)
    assert result["success"] is True
    assert result.get("task_state_restored") is True

    # File reverted...
    assert (work_dir / "main.py").read_text() == "v1\n"
    # ...AND the todo list reverted to the single in_progress item.
    restored = store.read()
    assert len(restored) == 1
    assert restored[0]["status"] == "in_progress"
    assert restored[0]["content"] == "task A"


def test_sidecar_written_for_checkpoint(work_dir, checkpoint_base):
    store = _todo_store()
    store.write([{"id": "1", "content": "x", "status": "pending"}])
    mgr = CheckpointManager(enabled=True, task_state_provider=lambda: store.read())
    assert mgr.ensure_checkpoint(str(work_dir), "s") is True
    # A sidecar JSON exists somewhere under the store's task_state dir.
    base = _store_path(checkpoint_base)
    dir_hash = _project_hash(str(work_dir))
    sidecar_dir = base / "task_state" / dir_hash
    assert sidecar_dir.exists()
    files = list(sidecar_dir.glob("*.json"))
    assert files, "expected a task-state sidecar JSON"


def test_no_hooks_is_backward_compatible(work_dir, checkpoint_base):
    """Without hooks, checkpoints are file-only and restore has no task_state key."""
    mgr = CheckpointManager(enabled=True)  # no task-state hooks
    assert mgr.ensure_checkpoint(str(work_dir), "s") is True
    cps = mgr.list_checkpoints(str(work_dir))
    commit = cps[0].get("hash") or cps[0].get("commit") or cps[0].get("id")
    (work_dir / "main.py").write_text("changed\n")
    result = mgr.restore(str(work_dir), commit)
    assert result["success"] is True
    assert "task_state_restored" not in result  # no hook → key absent
    assert (work_dir / "main.py").read_text() == "v1\n"


def test_task_state_provider_failure_does_not_break_checkpoint(work_dir, checkpoint_base):
    """A provider that raises must not fail the (file) checkpoint."""
    def _boom():
        raise RuntimeError("provider exploded")
    mgr = CheckpointManager(enabled=True, task_state_provider=_boom)
    # The file checkpoint still succeeds despite the provider raising.
    assert mgr.ensure_checkpoint(str(work_dir), "s") is True


def test_sidecar_survives_prune_and_orphans_cleaned(work_dir, checkpoint_base):
    """After pruning (which rewrites SHAs), surviving checkpoints keep their
    todos AND dropped-commit sidecars are deleted (no unbounded leak)."""
    store = _store_path(checkpoint_base)
    dir_hash = _project_hash(str(work_dir))
    todos = {"items": [{"id": "1", "content": "task", "status": "pending"}]}
    cur = {"v": list(todos["items"])}
    mgr = CheckpointManager(
        enabled=True, max_snapshots=2,
        task_state_provider=lambda: cur["v"],
    )
    # Take 5 checkpoints, each with a distinct file change → distinct commits.
    for i in range(5):
        (work_dir / "main.py").write_text(f"v{i}\n")
        cur["v"] = [{"id": "1", "content": f"task {i}", "status": "pending"}]
        mgr.new_turn()
        mgr.ensure_checkpoint(str(work_dir), f"snap-{i}")

    ts_dir = store / "task_state" / dir_hash
    sidecars = list(ts_dir.glob("*.json")) if ts_dir.exists() else []
    # max_snapshots=2 → at most ~2 sidecars survive (NOT 5 — orphans cleaned).
    assert len(sidecars) <= 3, f"sidecars leaked: {len(sidecars)}"

    # And the surviving checkpoints' todos are still restorable (SHA migration).
    cps = mgr.list_checkpoints(str(work_dir))
    assert cps
    restored = CheckpointManager(
        enabled=True,
        task_state_restorer=lambda t: cur.update(v=t),
    )
    result = restored.restore(str(work_dir), cps[0]["hash"])
    assert result["success"] is True
    # task_state_restored True means the sidecar survived the prune SHA-rewrite.
    assert result.get("task_state_restored") is True
