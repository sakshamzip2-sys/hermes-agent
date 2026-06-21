"""Declarative permission rules + permission modes (model-agnostic).

Ports the Claude Code *permission* concept into OpenComputer v2 as a
provider-neutral policy engine:

    permissions:
      mode: normal          # normal | plan | yolo
      allow: ["Bash(npm run *)", "Read(**)"]
      deny:  ["Bash(curl * | sh)", "Edit(/etc/**)", "Read(~/.ssh/**)"]
      ask:   ["Bash(git push *)"]

Each rule is a ``ToolName(specifier)`` pattern (bare ``ToolName`` matches any
invocation of that tool).  Tool names use the Claude vocabulary
(``Bash``/``Read``/``Edit``/``WebFetch``/...) which is mapped onto v2's native
tool names (``terminal``/``read_file``/``write_file``/``patch``/...); native v2
names and ``*`` are also accepted, so the language is neutral — it works for any
provider/model the user runs (OpenAI, Gemini, local, Anthropic).

This is **NOT a model tool** — the LLM never sees it.  Like checkpoints and the
approval guards, it is transparent policy infrastructure.  It is consumed at:

  * the central pre-tool-call gate
    (``hermes_cli.plugins.get_pre_tool_call_block_message``) — enforces
    ``deny`` rules and ``plan`` mode for *every* tool; honours ``allow`` as a
    whitelist that bypasses plan mode.
  * the terminal approval path (``tools.approval.check_all_command_guards``) —
    ``allow`` skips the dangerous-command prompt, ``ask`` forces one.

Precedence (deliberate, documented): ``deny`` > ``allow`` > ``plan-mode`` >
``ask``.  ``deny`` always wins (hard block).  An explicit ``allow`` whitelists a
pattern out of plan-mode blocking and out of ``ask``.  ``plan`` mode blocks
mutating tools that are not explicitly allowed.  ``ask`` forces an approval
prompt (terminal scope in this release).

Design constraints honoured:
  * prompt caching is sacred — enforcement is in the per-call tool gate (tool
    schemas/decisions are evaluated every request), never by mutating the cached
    system prompt mid-conversation.  ``mode`` is read live so ``/plan`` toggles
    take effect immediately without a cache rebuild.
  * config.yaml only — no new ``HERMES_*`` env vars for behaviour.
  * stdlib only — ``fnmatch``/``re``, no third-party deps.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ── Modes ────────────────────────────────────────────────────────────────────

MODE_NORMAL = "normal"
MODE_PLAN = "plan"
MODE_YOLO = "yolo"

# Accept a few friendly aliases for the mode value.
_MODE_ALIASES = {
    "default": MODE_NORMAL,
    "normal": MODE_NORMAL,
    "": MODE_NORMAL,
    "plan": MODE_PLAN,
    "planning": MODE_PLAN,
    "readonly": MODE_PLAN,
    "read-only": MODE_PLAN,
    "yolo": MODE_YOLO,
    "bypass": MODE_YOLO,
    "bypasspermissions": MODE_YOLO,
    "auto": MODE_YOLO,
}


def normalize_mode(mode) -> str:
    """Coerce a raw config/CLI mode value into one of the canonical modes."""
    if isinstance(mode, bool):
        # YAML 1.1: a bare ``yolo``/``off`` won't trip this, but guard anyway.
        return MODE_NORMAL
    if not isinstance(mode, str):
        return MODE_NORMAL
    return _MODE_ALIASES.get(mode.strip().lower(), MODE_NORMAL)


# ── Tool-name vocabulary (Claude name -> set of v2 tool names) ────────────────
#
# A rule's tool token is resolved through this table to the set of native v2
# tool names it covers.  Tokens not in the table are treated as a literal v2
# tool name (so a rule can target any tool by its real name).  ``*`` matches
# every tool.

_TOOL_GROUPS: Dict[str, set] = {
    "bash": {"terminal"},
    "shell": {"terminal"},
    "terminal": {"terminal"},
    "read": {"read_file", "read", "read_extract", "read_terminal"},
    "readfile": {"read_file"},
    "read_file": {"read_file"},
    "edit": {"write_file", "patch"},
    "write": {"write_file", "patch"},
    "writefile": {"write_file"},
    "write_file": {"write_file"},
    "patch": {"patch"},
    "str_replace": {"patch"},
    "webfetch": {"web_fetch", "read_url", "fetch_url"},
    "web_fetch": {"web_fetch"},
    "fetch": {"web_fetch", "read_url", "fetch_url"},
    "websearch": {"web_search", "search"},
    "web_search": {"web_search"},
    "browser": {"browser", "computer", "computer_use"},
    "skill": {"skill_run", "skill", "skill_view"},
    "task": {"delegate", "delegate_task"},
    "agent": {"delegate", "delegate_task"},
    "delegate": {"delegate", "delegate_task"},
    "memory": {"memory", "memory_tool"},
}

# Which tool *names* mutate persistent state (used by plan mode).  Terminal is
# handled specially (only destructive/mutating commands are blocked so the agent
# can still run read-only commands like ``ls``/``git status`` while planning).
_MUTATING_TOOLS = {
    "write_file",
    "patch",
    "str_replace",
    "create_file",
    "edit_file",
    "memory",
    "memory_tool",
    "skill_manage",
    "image_generation",
}

# Heuristic for "this terminal command mutates state" — used only by plan mode.
_TERMINAL_MUTATION_RE = re.compile(
    r"""(?xi)
    (^|\s|;|\||&|`|\$\()\s*
    (
        rm | rmdir | mv | cp | dd | mkdir | touch | truncate | shred | ln |
        chmod | chown | chgrp |
        tee | sed\s+-[a-z]*i | perl\s+-[a-z]*i | awk\b.*>\s*\S |
        git\s+(commit|push|reset|rebase|checkout|merge|clean|stash\s+drop|tag\s+-d|branch\s+-D) |
        (pip|pip3|uv|npm|pnpm|yarn|bun|cargo|go|brew|apt|apt-get|dnf|yum|pacman|gem)\s+(install|add|remove|uninstall|publish|i\b) |
        make\b | cmake\b | docker\s+(run|rm|rmi|build|push|compose) | kubectl\s+(apply|delete|create) |
        systemctl | service\b | crontab\b | mkfs | fdisk | parted |
        psql | mysql | sqlite3\b.*(insert|update|delete|drop|create) |
        curl\b.*-[a-zA-Z]*o\b | wget\b
    )\b
    """,
)
# Output redirection (> / >>) into a file is also a mutation.
_TERMINAL_REDIRECT_RE = re.compile(r"(?<![0-9&])>>?\s*[^\s&|;]")


def terminal_command_mutates(command: str) -> bool:
    """Best-effort: does this shell command mutate filesystem/system state?

    Conservative-but-permissive: read-only commands (ls, cat, grep, git status,
    find, head, tail, pwd, echo without redirect) return False so plan mode lets
    the agent research.  False negatives are acceptable here — plan mode is a
    productivity gate, not a security boundary (the security boundary is the
    OS/VM layer + the approval guards, which still run).
    """
    if not command or not isinstance(command, str):
        return False
    if _TERMINAL_MUTATION_RE.search(command):
        return True
    if _TERMINAL_REDIRECT_RE.search(command):
        return True
    # Catch quoted subshell mutations like ``bash -c "rm x"`` / ``sh -c 'mv a b'``
    # by re-scanning with quote characters stripped.  Cheap and closes the most
    # common plan-mode evasion.
    if ("-c" in command) and ('"' in command or "'" in command):
        dequoted = command.replace('"', " ").replace("'", " ")
        if _TERMINAL_MUTATION_RE.search(dequoted) or _TERMINAL_REDIRECT_RE.search(
            dequoted
        ):
            return True
    return False


# ── Rule model ────────────────────────────────────────────────────────────────


@dataclass
class Rule:
    """A single parsed permission rule."""

    raw: str
    action: str  # "allow" | "deny" | "ask"
    tool_token: str  # normalized rule tool token (lowercased) or "*"
    tools: frozenset  # resolved set of v2 tool names, or frozenset({"*"})
    specifier: Optional[str]  # raw specifier text, or None for tool-only match
    is_domain: bool = False
    _regex: Optional[re.Pattern] = field(default=None, repr=False)

    def matches(self, tool_name: str, target: str) -> bool:
        """Does this rule apply to a call to ``tool_name`` with ``target``?"""
        if "*" not in self.tools and tool_name not in self.tools:
            return False
        if self.specifier is None:
            return True  # tool-name-only rule matches any args
        if self.is_domain:
            host = _host_from_target(target)
            if not host:
                return False
            if _glob_match(self._regex, host):
                return True
            # Treat the bare apex as equivalent to its www. host so a rule like
            # ``domain:example.com`` also matches ``www.example.com`` — but never
            # the other way (``domain:example.com`` must NOT match a different
            # registrable domain).  Strip a leading www. and retry.
            if host.startswith("www.") and _glob_match(self._regex, host[4:]):
                return True
            return False
        # Path/command/url specifier: match the raw target and, for paths, the
        # user-expanded absolute form too (so ~/x and /home/u/x both hit).
        if _glob_match(self._regex, target):
            return True
        expanded = _expand_target(target)
        if expanded != target and _glob_match(self._regex, expanded):
            return True
        return False


_RULE_RE = re.compile(r"^\s*([A-Za-z_*][\w.*-]*)\s*(?:\((.*)\)\s*)?$", re.DOTALL)


def parse_rule(raw: str, action: str) -> Optional[Rule]:
    """Parse a ``ToolName(specifier)`` / bare ``ToolName`` rule string.

    Returns ``None`` (with a logged warning) for malformed entries so one bad
    rule never breaks the whole policy.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = _RULE_RE.match(raw.strip())
    if not m:
        logger.warning("permission_rules: ignoring malformed rule %r", raw)
        return None
    tool_token = (m.group(1) or "").strip().lower()
    specifier = m.group(2)
    if specifier is not None:
        specifier = specifier.strip()
        if specifier == "":
            specifier = None  # ``Tool()`` == bare ``Tool``

    if tool_token == "*":
        tools = frozenset({"*"})
    else:
        tools = frozenset(_TOOL_GROUPS.get(tool_token, {tool_token}))

    is_domain = False
    regex: Optional[re.Pattern] = None
    if specifier is not None:
        spec = specifier
        if spec.lower().startswith("domain:"):
            is_domain = True
            spec = spec.split(":", 1)[1].strip()
        regex = _compile_glob(spec)

    return Rule(
        raw=raw,
        action=action,
        tool_token=tool_token,
        tools=tools,
        specifier=specifier,
        is_domain=is_domain,
        _regex=regex,
    )


# ── Glob helpers ──────────────────────────────────────────────────────────────


def _compile_glob(pattern: str) -> re.Pattern:
    """Compile a shell-style glob to a regex (case-sensitive, full-string).

    ``fnmatch.translate`` makes ``*`` match across ``/`` which is what we want
    for both command and path specifiers (mirrors Claude's prefix-glob
    semantics, e.g. ``Bash(npm run *)``).
    """
    # Expand a leading ~ in path-style specifiers before translating.
    expanded = _expand_target(pattern)
    try:
        return re.compile(fnmatch.translate(expanded))
    except re.error:
        # Fall back to a literal match if the user pattern is pathological.
        return re.compile(re.escape(expanded) + r"\Z")


def _glob_match(regex: Optional[re.Pattern], value: str) -> bool:
    if regex is None or value is None:
        return False
    return regex.match(value) is not None


def _expand_target(value: str) -> str:
    if not isinstance(value, str) or not value:
        return value
    if value.startswith("~"):
        try:
            return os.path.expanduser(value)
        except Exception:
            return value
    return value


def _host_from_target(target: str) -> str:
    if not target:
        return ""
    t = target.strip()
    if "://" not in t:
        t = "//" + t  # let urlparse treat a bare host[:port]/path as netloc
    try:
        netloc = urlparse(t).netloc or ""
    except Exception:
        return ""
    # strip userinfo + port
    if "@" in netloc:
        netloc = netloc.split("@", 1)[1]
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    return netloc.lower()


# ── Target extraction (what the specifier matches against, per tool) ──────────

_PATH_TOOLS = {
    "read_file",
    "write_file",
    "patch",
    "read",
    "read_extract",
    "create_file",
    "edit_file",
}
_URL_TOOLS = {"web_fetch", "read_url", "fetch_url", "browser"}


def extract_target(tool_name: str, args: Optional[dict]) -> str:
    """Return the string a specifier should match for this tool call."""
    if not isinstance(args, dict):
        return ""
    if tool_name == "terminal":
        return str(args.get("command") or args.get("cmd") or "")
    if tool_name in _PATH_TOOLS:
        for key in ("path", "file_path", "file", "filename", "target_file"):
            v = args.get(key)
            if v:
                return str(v)
        return ""
    if tool_name in _URL_TOOLS:
        for key in ("url", "uri", "href", "domain"):
            v = args.get(key)
            if v:
                return str(v)
        return ""
    # Action-style tools (cronjob, skill_manage, memory, ...) carry their verb in
    # an ``action``/``operation``/``subcommand`` arg. Surfacing it lets a rule
    # like ``cronjob(remove)`` gate one verb without gating the whole tool, so a
    # destructive TOOL call can be put behind approval the same way a terminal
    # command is. Falls back to "" for tools with no such arg (bare-tool rules
    # still match those via specifier=None).
    for key in ("action", "operation", "subcommand", "cmd"):
        v = args.get(key)
        if v:
            return str(v)
    return ""


def is_mutating_tool(tool_name: str, args: Optional[dict]) -> bool:
    """Does this tool call mutate persistent state (for plan-mode blocking)?"""
    if tool_name in _MUTATING_TOOLS:
        return True
    if tool_name == "terminal":
        cmd = ""
        if isinstance(args, dict):
            cmd = str(args.get("command") or args.get("cmd") or "")
        return terminal_command_mutates(cmd)
    return False


# ── Policy ────────────────────────────────────────────────────────────────────


@dataclass
class PermissionPolicy:
    mode: str = MODE_NORMAL
    allow: List[Rule] = field(default_factory=list)
    deny: List[Rule] = field(default_factory=list)
    ask: List[Rule] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            self.mode == MODE_NORMAL
            and not self.allow
            and not self.deny
            and not self.ask
        )


def build_policy(perms: Optional[dict]) -> PermissionPolicy:
    """Build a :class:`PermissionPolicy` from a ``permissions`` config dict."""
    perms = perms if isinstance(perms, dict) else {}
    mode = normalize_mode(perms.get("mode", MODE_NORMAL))

    def _rules(key: str) -> List[Rule]:
        out: List[Rule] = []
        raw_list = perms.get(key) or []
        if isinstance(raw_list, str):
            raw_list = [raw_list]
        if not isinstance(raw_list, (list, tuple)):
            return out
        for entry in raw_list:
            rule = parse_rule(entry, key)
            if rule is not None:
                out.append(rule)
        return out

    return PermissionPolicy(
        mode=mode,
        allow=_rules("allow"),
        deny=_rules("deny"),
        ask=_rules("ask"),
    )


@dataclass
class Decision:
    action: str  # "deny" | "allow" | "ask" | "normal"
    reason: str = ""


def _first_match(rules: List[Rule], tool_name: str, target: str) -> Optional[Rule]:
    for rule in rules:
        try:
            if rule.matches(tool_name, target):
                return rule
        except Exception:  # noqa: BLE001 — a bad rule must never break dispatch
            continue
    return None


def decide(policy: PermissionPolicy, tool_name: str, args: Optional[dict]) -> Decision:
    """Resolve a tool call against the policy.

    Precedence: deny > allow > plan-mode > ask.
    """
    tool_name = (tool_name or "").strip()
    target = extract_target(tool_name, args)

    deny_hit = _first_match(policy.deny, tool_name, target)
    if deny_hit is not None:
        return Decision(
            "deny",
            f"Blocked by permissions.deny rule: {deny_hit.raw}"
            + (f" (matched {target!r})" if target else ""),
        )

    allow_hit = _first_match(policy.allow, tool_name, target)
    if allow_hit is not None:
        return Decision("allow", f"Allowed by permissions.allow rule: {allow_hit.raw}")

    if policy.mode == MODE_PLAN and is_mutating_tool(tool_name, args):
        return Decision(
            "deny",
            "Plan mode is on: this would modify files/state and is blocked. "
            "Propose the change, then exit plan mode (/accept-edits or set "
            "permissions.mode: normal) to apply it.",
        )

    ask_hit = _first_match(policy.ask, tool_name, target)
    if ask_hit is not None:
        return Decision("ask", f"Approval required by permissions.ask rule: {ask_hit.raw}")

    return Decision("normal", "")


# ── Live mode override (so /plan, /accept-edits, --plan take effect now) ───────
#
# The config supplies the *default* mode.  A CLI flag or slash command can
# override it per session (or process-wide).  Reading the override live keeps the
# enforcement immediate without rebuilding the cached system prompt.

_lock = threading.RLock()
_global_mode_override: Optional[str] = None
_session_mode_override: Dict[str, str] = {}


def set_global_mode(mode: Optional[str]) -> None:
    """Process-wide mode override (e.g. the ``--plan`` CLI flag at startup)."""
    global _global_mode_override
    with _lock:
        _global_mode_override = normalize_mode(mode) if mode is not None else None


def set_session_mode(session_id: str, mode: Optional[str]) -> None:
    """Per-session mode override (e.g. the ``/plan`` slash command)."""
    with _lock:
        if not session_id:
            set_global_mode(mode)
            return
        if mode is None:
            _session_mode_override.pop(session_id, None)
        else:
            _session_mode_override[session_id] = normalize_mode(mode)


def _ambient_session_key() -> str:
    """The approval-layer's current session key (a contextvar), or ''.

    The gateway sets this (``set_current_session_key``) to its per-conversation
    session key around each turn, and slash handlers like ``/yolo`` key
    per-session state off the same value.  The central plan-mode gate, however,
    is called with ``agent.session_id`` — which in the gateway is NOT the same
    string.  Consulting this contextvar lets a session-mode override set by a
    gateway ``/plan`` handler be seen by the gate regardless of which identifier
    the surface threads through.  Lazy import avoids a circular dependency
    (tools.approval imports permission_rules).
    """
    try:
        from tools.approval import get_current_session_key

        return get_current_session_key(default="") or ""
    except Exception:  # noqa: BLE001
        return ""


def get_effective_mode(session_id: str = "", config_mode: Optional[str] = None) -> str:
    """Resolve the live mode: session override > global override > config.

    Checks the passed ``session_id`` first (CLI: the agent's own id), then the
    ambient approval session key (gateway: the per-conversation key its slash
    handlers use), then the process-global override, then config.
    """
    with _lock:
        if session_id and session_id in _session_mode_override:
            return _session_mode_override[session_id]
    ambient = _ambient_session_key()
    with _lock:
        if ambient and ambient != session_id and ambient in _session_mode_override:
            return _session_mode_override[ambient]
        if _global_mode_override is not None:
            return _global_mode_override
    return normalize_mode(config_mode if config_mode is not None else MODE_NORMAL)


# ── Config access + public entry points ───────────────────────────────────────


def _load_permissions_config() -> dict:
    try:
        from hermes_cli.config import load_config

        config = load_config()
        return config.get("permissions", {}) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("permission_rules: could not load config: %s", exc)
        return {}


# Runtime rules layered on top of config — set by callers like headless
# ``--allowedTools`` / ``--disallowedTools``.  Merged into every live policy so
# a one-shot invocation can scope tools without editing config.yaml.
_runtime_rules: Dict[str, List[str]] = {"allow": [], "deny": [], "ask": []}


def set_runtime_rules(
    allow: Optional[List[str]] = None,
    deny: Optional[List[str]] = None,
    ask: Optional[List[str]] = None,
) -> None:
    """Layer extra rules on top of config (e.g. headless --allowedTools).

    Pass ``None`` to leave a bucket unchanged; pass ``[]`` to clear it.
    """
    with _lock:
        if allow is not None:
            _runtime_rules["allow"] = list(allow)
        if deny is not None:
            _runtime_rules["deny"] = list(deny)
        if ask is not None:
            _runtime_rules["ask"] = list(ask)


def clear_runtime_rules() -> None:
    with _lock:
        _runtime_rules["allow"] = []
        _runtime_rules["deny"] = []
        _runtime_rules["ask"] = []


def _merge_runtime(perms: dict) -> dict:
    """Return a copy of ``perms`` with runtime rules appended to each bucket."""
    with _lock:
        rt = {k: list(v) for k, v in _runtime_rules.items()}
    if not any(rt.values()):
        return perms
    merged = dict(perms or {})
    for kind in ("allow", "deny", "ask"):
        base = merged.get(kind) or []
        if isinstance(base, str):
            base = [base]
        merged[kind] = list(base) + rt.get(kind, [])
    return merged


def load_live_policy(session_id: str = "") -> PermissionPolicy:
    """Build the active policy from config, applying the live mode override."""
    perms = _merge_runtime(_load_permissions_config())
    policy = build_policy(perms)
    policy.mode = get_effective_mode(session_id, perms.get("mode", MODE_NORMAL))
    return policy


def current_mode(session_id: str = "") -> str:
    """The live permission mode (session override > global > config)."""
    try:
        return get_effective_mode(
            session_id, _load_permissions_config().get("mode", MODE_NORMAL)
        )
    except Exception:  # noqa: BLE001
        return MODE_NORMAL


# System-prompt block injected when the conversation is in plan mode.  Kept short
# so it costs little; the hard enforcement is the tool gate, this just tells the
# model what to expect so it proposes instead of trying (and getting blocked).
PLAN_MODE_SYSTEM_PROMPT = (
    "## Operating mode: PLAN\n"
    "You are in PLAN MODE. Investigate freely with read-only tools (read files, "
    "list, grep, run non-mutating shell commands), then PRESENT A PLAN for the "
    "user to approve. Do NOT modify files, write/patch, or run state-changing "
    "commands — those tools are blocked and will return a permission error until "
    "the user leaves plan mode (they run /accept-edits or set permissions.mode: "
    "normal). Lead with the plan; don't attempt mutations to 'test' whether "
    "they're allowed."
)


def evaluate_tool_call(
    tool_name: str, args: Optional[dict], session_id: str = ""
) -> Decision:
    """Resolve a tool call against the live policy.  Never raises."""
    try:
        policy = load_live_policy(session_id)
        if policy.is_empty():
            return Decision("normal", "")
        return decide(policy, tool_name, args)
    except Exception as exc:  # noqa: BLE001 — policy must never break dispatch
        logger.debug("permission_rules: evaluate failed (%s); allowing", exc)
        return Decision("normal", "")


def pre_tool_block_message(
    tool_name: str, args: Optional[dict], session_id: str = ""
) -> Optional[str]:
    """Block message for the central pre-tool-call gate, or ``None``.

    Enforces ``deny`` rules and ``plan`` mode for every tool.  ``allow``
    whitelists out of plan mode.  ``ask`` is NOT blocked here — it is handled by
    ``check_tool_approval`` (the gateway approval card), which gates both terminal
    commands and action-style tool calls like ``cronjob(remove)``.
    """
    decision = evaluate_tool_call(tool_name, args, session_id)
    if decision.action == "deny":
        return decision.reason
    return None
