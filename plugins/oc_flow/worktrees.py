"""Per-subagent git worktree isolation for flows.

v2 already isolates a whole CLI session with ``hermes -w``. This module brings
the same isolation down to an individual flow subagent: ``agent(prompt,
worktree=True)`` runs that subagent in its own ``.worktrees/<name>`` checkout on
a throwaway branch, so several agents editing files in ``parallel()`` never
collide. Conventions mirror ``cli.py::_setup_worktree`` (``.worktrees/`` dir,
``hermes/`` branch prefix, ``.worktreeinclude`` for gitignored files, and a
``.gitignore`` entry), so the two systems behave the same.

A worktree with no changes after the agent finishes is removed automatically; one
that has changes is kept (and its path logged) so the work can be merged.
"""

from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("hermes.plugins.oc_flow.worktrees")


def git_repo_root(start: Optional[str] = None) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10, cwd=start or None,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _ensure_gitignored(repo_root: Path) -> None:
    gitignore = repo_root / ".gitignore"
    entry = ".worktrees/"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if entry not in existing.splitlines():
            with open(gitignore, "a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(entry + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_flow worktree: could not update .gitignore: %s", exc)


def _copy_worktreeinclude(repo_root: Path, wt_path: Path) -> None:
    include_file = repo_root / ".worktreeinclude"
    if not include_file.exists():
        return
    import shutil

    repo_resolved = repo_root.resolve()
    wt_resolved = wt_path.resolve()
    for line in include_file.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        src = repo_root / entry
        dst = wt_path / entry
        try:
            src_resolved = src.resolve(strict=False)
            dst_resolved = dst.resolve(strict=False)
        except (OSError, ValueError):
            continue
        # Path-traversal / symlink-escape guard (same posture as cli.py).
        if not str(src_resolved).startswith(str(repo_resolved)):
            continue
        if not str(dst_resolved).startswith(str(wt_resolved)):
            continue
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            elif src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except Exception as exc:  # noqa: BLE001
            logger.debug("oc_flow worktree: copy of %s failed: %s", entry, exc)


def create_worktree(repo_root: Optional[str] = None, *, base_ref: str = "HEAD") -> Optional[Dict[str, str]]:
    """Create an isolated worktree. Returns {path, branch, repo_root} or None."""
    root = repo_root or git_repo_root()
    if not root:
        logger.debug("oc_flow worktree: not inside a git repo; skipping isolation")
        return None
    root_path = Path(root)
    short = uuid.uuid4().hex[:8]
    name = f"hermes-flow-{short}"
    branch = f"hermes/{name}"
    wt_dir = root_path / ".worktrees"
    wt_dir.mkdir(parents=True, exist_ok=True)
    wt_path = wt_dir / name

    _ensure_gitignored(root_path)
    try:
        res = subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch, base_ref],
            capture_output=True, text=True, timeout=60, cwd=root,
        )
        if res.returncode != 0:
            logger.debug("oc_flow worktree: add failed: %s", res.stderr.strip())
            return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_flow worktree: add raised: %s", exc)
        return None

    _copy_worktreeinclude(root_path, wt_path)
    return {"path": str(wt_path), "branch": branch, "repo_root": str(root)}


def worktree_has_changes(wt_path: str) -> bool:
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=30, cwd=wt_path,
        )
        return bool(res.stdout.strip())
    except Exception:
        return True  # if unsure, assume changes (keep the worktree)


def remove_worktree(wt_path: str, *, force: bool = False) -> bool:
    repo = git_repo_root(wt_path) or wt_path
    try:
        cmd = ["git", "worktree", "remove", wt_path]
        if force:
            cmd.append("--force")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=repo)
        return res.returncode == 0
    except Exception as exc:  # noqa: BLE001
        logger.debug("oc_flow worktree: remove failed: %s", exc)
        return False


def cleanup_if_unchanged(wt: Dict[str, str]) -> bool:
    """Remove the worktree iff it has no changes. Returns True if removed."""
    path = wt.get("path")
    if not path:
        return False
    if worktree_has_changes(path):
        logger.info("oc_flow: worktree kept (has changes): %s", path)
        return False
    return remove_worktree(path)
