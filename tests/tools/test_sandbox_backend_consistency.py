"""Consistency: every sandbox gate derives its 'isolated backend' set from the
single source of truth (sandbox_resolver.ISOLATED_BACKENDS), so a backend that
is isolated (e.g. e2b) is treated identically everywhere — no module silently
omits a backend.

Discriminator: a HARDLINE command (e.g. ``rm -rf /``). Inside an isolated
sandbox the approval gate short-circuits to *allowed* BEFORE the hardline floor
(the sandbox can't touch the host); on the host that command stays hard-blocked
regardless of interactivity. That gives a clean signal unaffected by the
non-interactive auto-approve path.
"""

from tools.sandbox_resolver import ISOLATED_BACKENDS


def test_isolated_backends_is_the_source_of_truth():
    assert {"docker", "singularity", "modal", "daytona", "e2b"} <= ISOLATED_BACKENDS


def test_hardline_allowed_in_e2b_sandbox_but_blocked_on_host():
    from tools.approval import check_dangerous_command

    # docker (known isolated) — allowed inside the sandbox
    assert check_dangerous_command("rm -rf /", env_type="docker").get("approved") is True
    # e2b is isolated too → MUST be treated identically (the bug: it was omitted)
    assert check_dangerous_command("rm -rf /", env_type="e2b").get("approved") is True
    # host stays hard-blocked (guard the guard)
    assert check_dangerous_command("rm -rf /", env_type="local").get("approved") is False


def test_execute_code_guard_allows_e2b_sandbox():
    from tools.approval import check_execute_code_guard

    # An isolated backend short-circuits to approved; assert e2b matches docker.
    assert (
        check_execute_code_guard("print(1)", env_type="e2b").get("approved")
        is check_execute_code_guard("print(1)", env_type="docker").get("approved")
        is True
    )
