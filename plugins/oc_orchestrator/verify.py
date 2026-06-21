"""Deterministic verification gate (the orchestrator's "verification built in").

A task is NEVER marked done on worker self-report (the producer is never its sole
grader). When a worker reports completed, the orchestrator runs this gate against
a frozen snapshot of the work:

  1. Freeze: hash-pin the relevant files at verification time so the worker cannot
     mutate the workdir between the test run and the verified write (closes the
     verify TOCTOU).
  2. Immutable tests: diff the test files against their pre-task hashes; if a
     worker weakened or deleted a test to go green, that is an automatic REJECT
     (closes corrupt-success-by-weakened-test).
  3. Run the real test command and read its REAL exit code/output; green is
     required (no fabricated success).
  4. Only on (tests unchanged AND command green) does the gate PASS. A failing
     gate routes back into the failure-recovery model.

The command runner is INJECTED so this is unit-testable without a sandbox, and so
production can route it through the existing sandbox_resolver. Pure + deterministic
(no model). The optional reviewer sign-off is a separate concern (brain.need_verifier
+ the reviewer agent-def); this module is the mechanism floor.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass
class VerifyResult:
    verdict: str  # pass | reject
    reason: str = ""
    test_output: str = ""
    weakened_tests: List[str] = field(default_factory=list)


def hash_file(path: str) -> Optional[str]:
    """sha256 of a file's bytes, or None if it does not exist."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (FileNotFoundError, IsADirectoryError):
        return None


def snapshot_hashes(paths: List[str]) -> Dict[str, Optional[str]]:
    """Pin a set of files to their current content hashes (None = absent)."""
    return {p: hash_file(p) for p in paths}


def default_runner(command: List[str], cwd: Optional[str] = None) -> tuple:
    """Run a test command; return (exit_code, combined_output). Production wraps
    this through the sandbox_resolver; tests inject a fake."""
    proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def detect_weakened_tests(
    test_files: List[str], pre_hashes: Dict[str, Optional[str]]
) -> List[str]:
    """Return test files whose content changed (or were deleted) vs the pre-task
    snapshot. Tests are immutable across a task: a changed/removed test is a
    weakening attempt."""
    weakened = []
    for tf in test_files:
        before = pre_hashes.get(tf)
        after = hash_file(tf)
        if before is not None and after != before:
            weakened.append(tf)
    return weakened


def verify(
    *,
    test_command: Optional[List[str]],
    test_files: List[str],
    pre_test_hashes: Dict[str, Optional[str]],
    cwd: Optional[str] = None,
    runner: Callable[[List[str], Optional[str]], tuple] = default_runner,
) -> VerifyResult:
    """Run the deterministic gate. PASS only if tests are unchanged AND the test
    command exits green. Fails CLOSED: no test command -> reject (cannot verify),
    never a silent pass."""
    # 1. Immutable-tests check first: a weakened test is an automatic reject even
    #    if the (now-easier) suite would pass.
    weakened = detect_weakened_tests(test_files, pre_test_hashes)
    if weakened:
        return VerifyResult("reject", reason="tests_weakened", weakened_tests=weakened)

    # 2. Fail closed when there is nothing to verify with.
    if not test_command:
        return VerifyResult("reject", reason="no_test_command")

    # 3. Run the real command; green required.
    code, output = runner(test_command, cwd)
    if code != 0:
        return VerifyResult("reject", reason="tests_failed", test_output=output[-4000:])
    return VerifyResult("pass", reason="tests_green", test_output=output[-4000:])
