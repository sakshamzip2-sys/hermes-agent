"""Reusable agent-type definitions — the v2 port of Claude-Code's ``.claude/agents``.

An *agent definition* is a Markdown file with YAML frontmatter whose body is the
agent's system prompt. It captures, once and by name, how a specialized agent
should be run (its prompt, restricted toolsets, model/provider, permission mode,
effort, memory scope, preloaded skills) so the same definition can be reused as:

  * a ``delegate_task`` subagent (``agent_type="code-reviewer"``),
  * an ``oc_teams`` teammate (``hermes team spawn … --agent code-reviewer``), and
  * an ``oc_agents`` background session (``hermes agents dispatch … --agent …``).

This module is **not a model tool** — the LLM never sees it (the same discipline
as ``tools/permission_rules.py``). It is a plain loader consumed by the existing
spawn seams via optional parameters, so the always-on tool schema is unchanged.

Definitions are discovered, most-specific first, from:

  1. ``<cwd>/.hermes/agents/*.md``   (project-scoped, shareable via VCS)
  2. ``<hermes-home>/agents/*.md``   (user-scoped, all projects)

The first definition found for a given (lowercased) ``name`` wins, so a project
definition overrides a user one. ``HERMES_AGENTS_DIR`` (os.pathsep-separated)
overrides the search path entirely — used by tests and power users.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("hermes.tools.agent_defs")


@dataclass
class AgentDefinition:
    """A named, file-backed specification for how to run a specialized agent."""

    name: str
    description: str = ""
    prompt: str = ""  # the Markdown body — the agent's system prompt / role guidance
    toolsets: Optional[List[str]] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    permission_mode: Optional[str] = None  # normal | plan | yolo (used by the plan-approval gate)
    skills: Optional[List[str]] = None
    memory: Optional[str] = None  # user | project | local (per-agent persistent memory scope)
    effort: Optional[str] = None  # low | medium | high | xhigh | max
    max_iterations: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def _split(text: str) -> Optional[tuple[Dict[str, Any], str]]:
    """Split a Markdown doc into (frontmatter mapping, body). None if no frontmatter."""
    if not isinstance(text, str):
        return None
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return None
    after_open = stripped[3:]
    end = after_open.find("\n---")
    if end == -1:
        return None
    fm_text = after_open[:end]
    body = after_open[end + len("\n---"):]
    # Drop the rest of the closing-fence line.
    nl = body.find("\n")
    body = body[nl + 1:] if nl != -1 else ""
    try:
        import yaml

        data = yaml.safe_load(fm_text)
    except Exception as exc:  # pragma: no cover - malformed YAML
        logger.debug("agent_defs: frontmatter parse failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return data, body.strip()


def _as_list(value: Any) -> Optional[List[str]]:
    """Normalize a list-or-comma-string frontmatter value into a list of strings."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
        return [v for v in items if v] or None
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
        return [v for v in items if v] or None
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def parse_agent_definition(text: str, *, source_path: Optional[str] = None) -> Optional[AgentDefinition]:
    """Parse one agent-definition document. Returns None if it has no ``name``."""
    split = _split(text)
    if split is None:
        return None
    fm, body = split

    name = _opt_str(fm.get("name"))
    if not name:
        return None
    name = name.lower()

    # `toolsets:` is preferred; `tools:` is accepted as the Claude-Code alias.
    toolsets = _as_list(fm.get("toolsets"))
    if toolsets is None:
        toolsets = _as_list(fm.get("tools"))

    known = {"name", "description", "toolsets", "tools", "model", "provider",
             "permissionmode", "permission_mode", "skills", "memory", "effort",
             "maxturns", "max_iterations", "max_turns"}
    extra = {k: v for k, v in fm.items() if str(k).lower() not in known}

    return AgentDefinition(
        name=name,
        description=_opt_str(fm.get("description")) or "",
        prompt=body,
        toolsets=toolsets,
        model=_opt_str(fm.get("model")),
        provider=_opt_str(fm.get("provider")),
        permission_mode=_opt_str(fm.get("permissionMode") or fm.get("permission_mode")),
        skills=_as_list(fm.get("skills")),
        memory=_opt_str(fm.get("memory")),
        effort=_opt_str(fm.get("effort")),
        max_iterations=_as_int(fm.get("maxTurns") if fm.get("maxTurns") is not None
                               else fm.get("max_iterations") if fm.get("max_iterations") is not None
                               else fm.get("max_turns")),
        extra=extra,
        source_path=source_path,
    )


# --------------------------------------------------------------------------- #
# Discovery + loading
# --------------------------------------------------------------------------- #

def agents_dirs() -> List[Path]:
    """Return the definition search roots, most-specific first.

    ``HERMES_AGENTS_DIR`` (os.pathsep-separated) overrides the defaults.
    """
    override = os.environ.get("HERMES_AGENTS_DIR", "").strip()
    if override:
        return [Path(p).expanduser() for p in override.split(os.pathsep) if p.strip()]
    dirs: List[Path] = [Path.cwd() / ".hermes" / "agents"]
    try:
        from hermes_constants import get_hermes_home

        dirs.append(get_hermes_home() / "agents")
    except Exception:  # pragma: no cover - hermes_constants always importable in-tree
        dirs.append(Path(os.path.expanduser("~/.hermes")) / "agents")
    return dirs


def load_agent_definitions(dirs: Optional[List[Path]] = None) -> Dict[str, AgentDefinition]:
    """Load every ``*.md`` definition under *dirs*, keyed by name.

    Dirs are scanned in order; the first definition for a given name wins, so an
    earlier (more specific) directory overrides a later one. Missing dirs and
    unparseable files are skipped, never fatal.
    """
    roots = dirs if dirs is not None else agents_dirs()
    out: Dict[str, AgentDefinition] = {}
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            d = parse_agent_definition(text, source_path=str(path))
            if d is not None and d.name not in out:
                out[d.name] = d
    return out


def get_agent_definition(name: str, dirs: Optional[List[Path]] = None) -> Optional[AgentDefinition]:
    """Resolve a single definition by name (case-insensitive), or None."""
    if not name:
        return None
    return load_agent_definitions(dirs).get(name.strip().lower())


def list_agent_definitions(dirs: Optional[List[Path]] = None) -> List[AgentDefinition]:
    """Return all loaded definitions, sorted by name."""
    return sorted(load_agent_definitions(dirs).values(), key=lambda d: d.name)
