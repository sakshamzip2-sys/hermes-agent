"""Declarative permission-rule engine (tools/permission_rules.py).

Ports Claude Code's permission concept (allow/deny/ask rules + plan mode over
tools & bash/path/url patterns) into v2 as a model-agnostic policy engine. These
tests pin the behaviour contract: precedence (deny > allow > plan > ask), the
Claude→v2 tool-name vocabulary, glob/path/domain matching, plan-mode mutation
detection, and the fail-open guarantees.
"""

from __future__ import annotations

from tools import permission_rules as pr


# --------------------------------------------------------------------------
# mode normalisation
# --------------------------------------------------------------------------
class TestNormalizeMode:
    def test_aliases(self):
        assert pr.normalize_mode("normal") == pr.MODE_NORMAL
        assert pr.normalize_mode("default") == pr.MODE_NORMAL
        assert pr.normalize_mode("") == pr.MODE_NORMAL
        assert pr.normalize_mode("plan") == pr.MODE_PLAN
        assert pr.normalize_mode("read-only") == pr.MODE_PLAN
        assert pr.normalize_mode("yolo") == pr.MODE_YOLO
        assert pr.normalize_mode("bypass") == pr.MODE_YOLO

    def test_garbage_defaults_to_normal(self):
        assert pr.normalize_mode("nonsense") == pr.MODE_NORMAL
        assert pr.normalize_mode(None) == pr.MODE_NORMAL
        assert pr.normalize_mode(True) == pr.MODE_NORMAL


# --------------------------------------------------------------------------
# rule parsing + tool vocabulary
# --------------------------------------------------------------------------
class TestParseRule:
    def test_bash_specifier_maps_to_terminal(self):
        rule = pr.parse_rule("Bash(npm run *)", "allow")
        assert rule is not None
        assert "terminal" in rule.tools
        assert rule.specifier == "npm run *"

    def test_bare_tool_matches_any_args(self):
        rule = pr.parse_rule("web_search", "deny")
        assert rule is not None and rule.specifier is None
        assert rule.matches("web_search", "")

    def test_read_maps_to_read_file(self):
        rule = pr.parse_rule("Read(/etc/**)", "deny")
        assert "read_file" in rule.tools

    def test_edit_maps_to_write_and_patch(self):
        rule = pr.parse_rule("Edit(*.py)", "ask")
        assert {"write_file", "patch"} <= rule.tools

    def test_star_matches_every_tool(self):
        rule = pr.parse_rule("*", "deny")
        assert rule.matches("anything", "")

    def test_malformed_rule_returns_none(self):
        assert pr.parse_rule("", "allow") is None
        assert pr.parse_rule("()", "allow") is None


# --------------------------------------------------------------------------
# precedence: deny > allow > plan > ask
# --------------------------------------------------------------------------
def _policy(*, mode="normal", allow=(), deny=(), ask=()):
    return pr.build_policy(
        {"mode": mode, "allow": list(allow), "deny": list(deny), "ask": list(ask)}
    )


class TestDecidePrecedence:
    def test_deny_blocks_terminal_command(self):
        pol = _policy(deny=["Bash(rm *)"])
        d = pr.decide(pol, "terminal", {"command": "rm -rf /tmp/x"})
        assert d.action == "deny"

    def test_deny_beats_allow(self):
        pol = _policy(allow=["Bash(*)"], deny=["Bash(rm *)"])
        d = pr.decide(pol, "terminal", {"command": "rm -rf /"})
        assert d.action == "deny"

    def test_allow_whitelists_out_of_plan_mode(self):
        pol = _policy(mode="plan", allow=["Bash(npm run *)"])
        d = pr.decide(pol, "terminal", {"command": "npm run build"})
        assert d.action == "allow"

    def test_plan_mode_blocks_mutating_tool(self):
        pol = _policy(mode="plan")
        assert pr.decide(pol, "write_file", {"path": "x.py"}).action == "deny"

    def test_plan_mode_allows_read_only(self):
        pol = _policy(mode="plan")
        assert pr.decide(pol, "read_file", {"path": "x.py"}).action == "normal"
        assert pr.decide(pol, "terminal", {"command": "ls -la"}).action == "normal"

    def test_plan_mode_blocks_mutating_terminal_command(self):
        pol = _policy(mode="plan")
        assert pr.decide(pol, "terminal", {"command": "rm x"}).action == "deny"
        assert pr.decide(pol, "terminal", {"command": "echo hi > out.txt"}).action == "deny"

    def test_ask_rule(self):
        pol = _policy(ask=["Bash(git push *)"])
        assert pr.decide(pol, "terminal", {"command": "git push origin main"}).action == "ask"

    def test_empty_policy_is_normal(self):
        assert pr.decide(_policy(), "terminal", {"command": "rm -rf /"}).action == "normal"


# --------------------------------------------------------------------------
# target matching: paths, ~ expansion, domains
# --------------------------------------------------------------------------
class TestTargetMatching:
    def test_path_glob_deny(self):
        pol = _policy(deny=["Read(/etc/**)"])
        assert pr.decide(pol, "read_file", {"path": "/etc/passwd"}).action == "deny"
        assert pr.decide(pol, "read_file", {"path": "/home/u/x"}).action == "normal"

    def test_home_tilde_expansion(self):
        # ~/.ssh/** should match the expanded absolute path the tool receives.
        import os

        pol = _policy(deny=["Read(~/.ssh/**)"])
        ssh = os.path.expanduser("~/.ssh/id_rsa")
        assert pr.decide(pol, "read_file", {"path": ssh}).action == "deny"

    def test_domain_rule(self):
        pol = _policy(deny=["WebFetch(domain:evil.com)"])
        assert pr.decide(pol, "web_fetch", {"url": "https://evil.com/x"}).action == "deny"
        assert pr.decide(pol, "web_fetch", {"url": "https://safe.com/x"}).action == "normal"


# --------------------------------------------------------------------------
# public entry points + live overrides
# --------------------------------------------------------------------------
class TestPublicEntryPoints:
    def test_evaluate_tool_call_reads_config(self, monkeypatch):
        monkeypatch.setattr(
            pr, "_load_permissions_config", lambda: {"deny": ["Bash(rm *)"]}
        )
        pr.set_global_mode(None)  # clear any leaked override
        d = pr.evaluate_tool_call("terminal", {"command": "rm -rf /"})
        assert d.action == "deny"

    def test_pre_tool_block_message_returns_reason_on_deny(self, monkeypatch):
        monkeypatch.setattr(
            pr, "_load_permissions_config", lambda: {"deny": ["web_search"]}
        )
        pr.set_global_mode(None)
        msg = pr.pre_tool_block_message("web_search", {})
        assert msg and "web_search" in msg

    def test_pre_tool_block_message_none_when_allowed(self, monkeypatch):
        monkeypatch.setattr(pr, "_load_permissions_config", lambda: {})
        pr.set_global_mode(None)
        assert pr.pre_tool_block_message("terminal", {"command": "ls"}) is None

    def test_session_mode_override_takes_effect(self, monkeypatch):
        monkeypatch.setattr(pr, "_load_permissions_config", lambda: {})
        pr.set_session_mode("sess-1", "plan")
        try:
            d = pr.evaluate_tool_call("write_file", {"path": "x"}, session_id="sess-1")
            assert d.action == "deny"  # plan mode blocks the mutating write
            # a different session is unaffected
            assert pr.evaluate_tool_call("write_file", {"path": "x"}, session_id="other").action == "normal"
        finally:
            pr.set_session_mode("sess-1", None)

    def test_evaluate_never_raises(self, monkeypatch):
        def _boom():
            raise RuntimeError("config blew up")

        monkeypatch.setattr(pr, "_load_permissions_config", _boom)
        # Must fail open, not propagate.
        assert pr.evaluate_tool_call("terminal", {"command": "rm -rf /"}).action == "normal"


# --------------------------------------------------------------------------
# mutation heuristic (drives plan-mode terminal blocking)
# --------------------------------------------------------------------------
class TestMutationHeuristic:
    def test_mutating_commands(self):
        for cmd in (
            "rm -rf x",
            "mv a b",
            "echo hi > out.txt",
            "pip install requests",
            "git commit -m x",
            "git push origin main",
            "mkdir foo",
            "sed -i 's/a/b/' f",
            "docker build .",
        ):
            assert pr.terminal_command_mutates(cmd) is True, cmd

    def test_readonly_commands(self):
        for cmd in (
            "ls",
            "cat f",
            "grep x y",
            "git status",
            "git diff",
            "head -n 5 f",
            "echo hi",
            "find . -name '*.py'",
            "pwd",
        ):
            assert pr.terminal_command_mutates(cmd) is False, cmd


# --------------------------------------------------------------------------
# robustness + model-agnostic guarantees
# --------------------------------------------------------------------------
class TestRobustness:
    def test_one_bad_rule_does_not_break_policy(self, monkeypatch):
        monkeypatch.setattr(
            pr, "_load_permissions_config", lambda: {"deny": ["((((", "Bash(rm *)"]}
        )
        pr.set_global_mode(None)
        assert pr.evaluate_tool_call("terminal", {"command": "rm x"}).action == "deny"

    def test_decide_handles_none_args(self):
        pol = _policy(deny=["Bash(*)"])
        assert pr.decide(pol, "terminal", None).action == "deny"

    def test_domain_www_equivalence(self):
        """domain:example.com matches www.example.com (apex<->www), not other domains."""
        pol = _policy(deny=["WebFetch(domain:evil.com)"])
        assert pr.decide(pol, "web_fetch", {"url": "https://www.evil.com/x"}).action == "deny"
        assert pr.decide(pol, "web_fetch", {"url": "https://evil.com/x"}).action == "deny"
        assert pr.decide(pol, "web_fetch", {"url": "https://notevil.com/x"}).action == "normal"

    def test_domain_subdomain_glob_does_not_match_apex(self):
        """*.facebook.com must NOT match the bare apex facebook.com."""
        pol = _policy(deny=["WebFetch(domain:*.facebook.com)"])
        assert pr.decide(pol, "web_fetch", {"url": "https://facebook.com/x"}).action == "normal"
        assert pr.decide(pol, "web_fetch", {"url": "https://m.facebook.com/x"}).action == "deny"

    def test_plan_mode_catches_quoted_subshell_mutation(self):
        """bash -c \"rm ...\" must be caught by plan mode; bash -c \"ls\" must not."""
        pol = _policy(mode="plan")
        assert pr.decide(pol, "terminal", {"command": 'bash -c "rm /tmp/x"'}).action == "deny"
        assert pr.decide(pol, "terminal", {"command": "sh -c 'mv a b'"}).action == "deny"
        assert pr.decide(pol, "terminal", {"command": 'bash -c "ls -la"'}).action == "normal"

    def test_runtime_rules_layer_on_config(self, monkeypatch):
        """set_runtime_rules layers allow/deny on top of config (headless --allowedTools)."""
        monkeypatch.setattr(pr, "_load_permissions_config", lambda: {})
        pr.set_global_mode(None)
        try:
            pr.set_runtime_rules(deny=["Bash(rm *)"])
            assert pr.evaluate_tool_call("terminal", {"command": "rm x"}).action == "deny"
            assert pr.evaluate_tool_call("terminal", {"command": "ls"}).action == "normal"
        finally:
            pr.clear_runtime_rules()
        # cleared
        assert pr.evaluate_tool_call("terminal", {"command": "rm x"}).action == "normal"

    def test_runtime_rules_set_and_clear(self, monkeypatch):
        """set_runtime_rules layers on; clear_runtime_rules fully resets (oneshot cleanup)."""
        monkeypatch.setattr(pr, "_load_permissions_config", lambda: {})
        pr.set_global_mode(None)
        pr.clear_runtime_rules()
        try:
            pr.set_runtime_rules(allow=["Bash(npm *)"], deny=["Bash(rm *)"])
            assert pr.evaluate_tool_call("terminal", {"command": "rm x"}).action == "deny"
            assert pr.evaluate_tool_call("terminal", {"command": "npm test"}).action == "allow"
        finally:
            pr.clear_runtime_rules()
        # After clear, no runtime rule leaks.
        assert pr.evaluate_tool_call("terminal", {"command": "rm x"}).action == "normal"
        assert pr.evaluate_tool_call("terminal", {"command": "npm test"}).action == "normal"


class TestPlanModeSystemPromptTier:
    """Plan-mode prompt must live in the VOLATILE tier so the STABLE tier (the
    cacheable prefix) is byte-identical whether or not plan mode is on."""

    class _Agent:
        def __getattr__(self, name):
            return None
        session_id = "tier-sess"
        load_soul_identity = False
        skip_context_files = True
        valid_tool_names = set()
        _task_completion_guidance = False

    def test_stable_tier_byte_identical_regardless_of_plan_mode(self):
        import agent.system_prompt as sp

        pr.set_session_mode("tier-sess", None)
        normal = sp.build_system_prompt_parts(self._Agent())
        pr.set_session_mode("tier-sess", "plan")
        try:
            plan = sp.build_system_prompt_parts(self._Agent())
            # Stable prefix unchanged → existing users' prompt cache not broken.
            assert plan["stable"] == normal["stable"]
            # Plan instruction landed in the volatile tier instead.
            assert "PLAN MODE" in plan["volatile"].upper()
            assert "PLAN MODE" not in normal["volatile"].upper()
        finally:
            pr.set_session_mode("tier-sess", None)


    def test_no_vendor_name_hardcoded(self):
        """Standing rule: every feature must be model-agnostic — no vendor names."""
        import os

        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        path = os.path.join(repo_root, "tools", "permission_rules.py")
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read().lower()
        # "Claude Code" attribution in the docstring is fine; vendor names used as
        # logic/dependencies are not.  Check for telltale model/provider tokens.
        for vendor in ("import anthropic", "import openai", "claude-", "gpt-4", "gemini-", "opus", "sonnet"):
            assert vendor not in src, f"vendor token {vendor!r} leaked into the engine"
