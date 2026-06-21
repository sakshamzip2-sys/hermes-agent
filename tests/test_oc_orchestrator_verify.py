"""Tests for the deterministic verification gate (verify.py).

The orchestrator's "verification built in": never trust worker self-report. Proves
the gate passes only on green tests with unchanged test files, rejects weakened
tests (even if the suite would then pass), rejects red tests, fails closed with no
command, and the freeze (hash-pin) catches a mid-run mutation. Real file IO + a
real subprocess for the runner; the command runner is injected where a fake is
clearer. No model.
"""

from __future__ import annotations

import sys

import pytest

from plugins.oc_orchestrator import verify


def _green_runner(cmd, cwd=None):
    return 0, "1 passed"


def _red_runner(cmd, cwd=None):
    return 1, "1 failed: AssertionError"


def test_pass_on_green_tests_unchanged():
    r = verify.verify(test_command=["pytest"], test_files=[], pre_test_hashes={},
                      runner=_green_runner)
    assert r.verdict == "pass" and r.reason == "tests_green"


def test_reject_on_red_tests():
    r = verify.verify(test_command=["pytest"], test_files=[], pre_test_hashes={},
                      runner=_red_runner)
    assert r.verdict == "reject" and r.reason == "tests_failed"
    assert "AssertionError" in r.test_output


def test_fail_closed_with_no_command():
    r = verify.verify(test_command=None, test_files=[], pre_test_hashes={},
                      runner=_green_runner)
    assert r.verdict == "reject" and r.reason == "no_test_command"


def test_weakened_test_is_auto_reject(tmp_path):
    tf = tmp_path / "test_x.py"
    tf.write_text("def test_x():\n    assert add(2,3) == 5\n")
    pre = verify.snapshot_hashes([str(tf)])
    # Worker weakens the test to make a broken impl pass.
    tf.write_text("def test_x():\n    assert True\n")
    r = verify.verify(test_command=["pytest"], test_files=[str(tf)],
                      pre_test_hashes=pre, runner=_green_runner)  # would be green
    assert r.verdict == "reject" and r.reason == "tests_weakened"
    assert str(tf) in r.weakened_tests


def test_unchanged_test_passes_weakening_check(tmp_path):
    tf = tmp_path / "test_x.py"
    tf.write_text("def test_x():\n    assert add(2,3) == 5\n")
    pre = verify.snapshot_hashes([str(tf)])
    # Worker fixed the IMPL, left the test alone -> not weakened.
    r = verify.verify(test_command=["pytest"], test_files=[str(tf)],
                      pre_test_hashes=pre, runner=_green_runner)
    assert r.verdict == "pass"


def test_deleted_test_is_weakening(tmp_path):
    tf = tmp_path / "test_x.py"
    tf.write_text("def test_x():\n    assert 1 == 1\n")
    pre = verify.snapshot_hashes([str(tf)])
    tf.unlink()  # worker deleted the test
    weakened = verify.detect_weakened_tests([str(tf)], pre)
    assert str(tf) in weakened


def test_real_subprocess_runner_end_to_end(tmp_path):
    # A real green and a real red command through default_runner.
    code, _ = verify.default_runner([sys.executable, "-c", "import sys; sys.exit(0)"])
    assert code == 0
    code, _ = verify.default_runner([sys.executable, "-c", "import sys; sys.exit(1)"])
    assert code == 1
