"""skill_run — execute a skill honouring its ``context`` frontmatter.

Ports the v1 OpenComputer ``SkillTool`` execution semantics into v2 the v2 way
(a registry tool at the edge, not a core change).

v2 already exposes skills for INLINE use via ``skill_view`` (the model reads a
SKILL.md and follows it). The capability v2 was missing is frontmatter-driven
EXECUTION — a skill that declares ``context: fork`` should run in an isolated
subagent instead of inline. ``skill_run`` adds exactly that:

  - ``context: fork``  → delegate the skill's instructions to a subagent via
    ``delegate_task`` (optionally restricted to the skill's declared
    ``toolsets``), returning the subagent's result. Use for self-contained,
    multi-step skills that should not pollute the parent context.
  - ``context: inline`` (default) → return the skill body for the parent agent
    to follow directly (same outcome as ``skill_view``).

Known limitation vs v1: v2's ``delegate_task`` has no per-call model override,
so a skill's ``model:`` frontmatter is surfaced as a note rather than applied.
Supporting it faithfully would require a ``model`` parameter on ``delegate_task``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from tools.registry import registry
from tools.skills_tool import (
    SKILLS_DIR,
    _EXCLUDED_SKILL_DIRS,
    _is_skill_disabled,
    _parse_frontmatter,
    check_skills_requirements,
    skill_matches_environment,
    skill_matches_platform,
)


SKILL_RUN_SCHEMA = {
    "type": "function",
    "function": {
        "name": "skill_run",
        "description": (
            "Execute a skill by name, honouring its `context` frontmatter. "
            "Skills declaring `context: fork` run in an isolated subagent (use "
            "this for self-contained, multi-step skills that shouldn't pollute "
            "the main conversation); `context: inline` (the default) returns the "
            "skill body for you to follow directly. Use `skill_view` to simply "
            "read a skill; use `skill_run` when a skill is meant to be executed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the skill to run (as shown by skills_list).",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional extra context/inputs handed to the skill when it "
                        "forks into a subagent (ignored for inline skills)."
                    ),
                },
            },
            "required": ["name"],
        },
    },
}


def _resolve_skill(name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Return ``(frontmatter, body)`` for the named skill, or ``(None, None)``.

    Resolves with the same helpers ``_find_all_skills`` uses so local and
    external skill dirs both work; reads the FULL SKILL.md (not the truncated
    preview) because the body becomes the subagent's task on the fork path.
    """
    from agent.skill_utils import get_external_skills_dirs, iter_skill_index_files

    dirs = []
    if SKILLS_DIR.exists():
        dirs.append(SKILLS_DIR)
    dirs.extend(get_external_skills_dirs())

    for scan_dir in dirs:
        for skill_md in iter_skill_index_files(scan_dir, "SKILL.md"):
            if any(part in _EXCLUDED_SKILL_DIRS for part in skill_md.parts):
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            frontmatter, body = _parse_frontmatter(content)
            fm_name = str(frontmatter.get("name") or skill_md.parent.name)
            if fm_name != name:
                continue
            # Mirror skill_view/_find_all_skills gating so skill_run can't run a
            # skill that is hidden everywhere else: wrong OS, wrong environment,
            # or disabled for this profile.
            if not skill_matches_platform(frontmatter):
                return None, "__platform__"
            if not skill_matches_environment(frontmatter):
                return None, "__environment__"
            if _is_skill_disabled(fm_name):
                return None, "__disabled__"
            return frontmatter, body
    return None, None


def _normalize_toolsets(frontmatter: Dict[str, Any]) -> Optional[List[str]]:
    """Restrict the subagent to the skill's declared ``toolsets``.

    Only the v2-native ``toolsets`` key (TOOLSET GROUP names like ``web`` /
    ``terminal``) is honoured — ``delegate_task`` intersects against group
    names, so v1's ``tools:`` list of INDIVIDUAL tool names would be silently
    dropped and is intentionally NOT mapped here. A ported skill that wants to
    restrict the fork should declare ``toolsets:`` with group names; otherwise
    the subagent inherits the parent's default toolsets.
    """
    toolsets = frontmatter.get("toolsets")
    if isinstance(toolsets, str):
        return [toolsets]
    if isinstance(toolsets, list) and toolsets:
        return [str(t) for t in toolsets]
    return None


def _skill_run_impl(
    name: str,
    context: Optional[str] = None,
    *,
    task_id: Optional[str] = None,
    parent_agent: Any = None,
) -> str:
    name = (name or "").strip()
    if not name:
        return json.dumps(
            {"success": False, "error": "name is required"}, ensure_ascii=False
        )

    # Qualified plugin skills ("plugin:skill") are resolved by skill_view's
    # plugin path, not by skill_run's flat-tree scan. Fail clearly instead of a
    # confusing "not found".
    if ":" in name:
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"skill_run does not support qualified plugin skills "
                    f"({name!r}); read it with skill_view and follow it inline."
                ),
            },
            ensure_ascii=False,
        )

    frontmatter, body = _resolve_skill(name)
    if frontmatter is None:
        # _resolve_skill returns a sentinel string in `body` to distinguish why.
        _reason = {
            "__platform__": f"skill {name!r} is not available on this platform.",
            "__environment__": f"skill {name!r} is not available in this environment.",
            "__disabled__": f"skill {name!r} is disabled for this profile.",
        }.get(body or "", f"skill {name!r} not found")
        return json.dumps({"success": False, "error": _reason}, ensure_ascii=False)

    mode = str(frontmatter.get("context", "inline")).strip().lower()
    if mode not in ("inline", "fork"):
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"skill {name!r} declares unknown context={mode!r}; "
                    "expected 'inline' or 'fork'."
                ),
            },
            ensure_ascii=False,
        )

    def _bump():
        try:
            from tools.skill_usage import bump_use

            bump_use(name)
        except Exception:
            pass

    if mode == "inline":
        _bump()
        return json.dumps(
            {"success": True, "name": name, "context": "inline", "content": body or ""},
            ensure_ascii=False,
        )

    # context == "fork" → run in an isolated subagent.
    from tools.delegate_tool import delegate_task

    # delegate_task requires a parent agent; without one it RETURNS (not raises)
    # an error, which we'd otherwise wrap as success. Guard up front.
    if parent_agent is None:
        return json.dumps(
            {
                "success": False,
                "error": (
                    f"skill {name!r} uses context: fork, which requires an agent "
                    "context (run it through the agent, not standalone)."
                ),
            },
            ensure_ascii=False,
        )

    toolsets = _normalize_toolsets(frontmatter)
    goal_parts = [
        f"Execute the skill '{name}'. Follow its instructions exactly:",
        "",
        (body or "").strip(),
    ]
    if context and context.strip():
        goal_parts += ["", "Additional context from the caller:", context.strip()]
    if frontmatter.get("model"):
        goal_parts += [
            "",
            (
                f"[Note: this skill requests model '{frontmatter.get('model')}', but "
                "the subagent runs on the default model — per-call model override is "
                "not supported by delegate_task.]"
            ),
        ]
    goal = "\n".join(goal_parts)

    try:
        result = delegate_task(goal=goal, toolsets=toolsets, parent_agent=parent_agent)
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"success": False, "error": f"fork failed: {type(e).__name__}: {e}"},
            ensure_ascii=False,
        )

    # delegate_task returns a JSON-encoded string — parse it so we embed a
    # structured object (not a doubly-encoded blob) and can surface its own
    # success/error state instead of masking it.
    parsed: Any = result
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
        except (ValueError, TypeError):
            parsed = result
    if isinstance(parsed, dict) and parsed.get("success") is False:
        return json.dumps(
            {
                "success": False,
                "name": name,
                "context": "fork",
                "error": parsed.get("error", "delegate_task failed"),
            },
            ensure_ascii=False,
        )

    _bump()
    return json.dumps(
        {"success": True, "name": name, "context": "fork", "result": parsed},
        ensure_ascii=False,
    )


def skill_run(
    name: str,
    context: Optional[str] = None,
    *,
    task_id: Optional[str] = None,
    parent_agent: Any = None,
) -> str:
    """Public skill_run: times the execution and records the outcome to the usage
    store (success + latency) so the Skill Health view and the router tie-break
    have a real track record. Telemetry is best-effort and never alters the
    result returned to the caller."""
    import time

    t0 = time.monotonic()
    result = _skill_run_impl(name, context, task_id=task_id, parent_agent=parent_agent)
    try:
        latency_ms = int((time.monotonic() - t0) * 1000)
        success = True
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and parsed.get("success") is False:
                success = False
        except (ValueError, TypeError):
            pass
        nm = (name or "").strip()
        if nm and ":" not in nm:
            from tools.skill_usage import record_run

            record_run(nm, success=success, latency_ms=latency_ms)
    except Exception:  # noqa: BLE001 — telemetry must never break a skill run
        pass
    return result


registry.register(
    name="skill_run",
    toolset="skills",
    schema=SKILL_RUN_SCHEMA,
    handler=lambda args, **kw: skill_run(
        name=args.get("name"),
        context=args.get("context"),
        task_id=kw.get("task_id"),
        parent_agent=kw.get("parent_agent"),
    ),
    check_fn=check_skills_requirements,
    emoji="⚙️",
)
