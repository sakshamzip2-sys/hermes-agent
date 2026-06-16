"""Typed reference for the helpers injected into every flow script.

A flow script never imports these at runtime — the engine injects them as
module globals before the script runs. This module exists so flow authors get
editor autocomplete and type-checking by importing the names under a
``TYPE_CHECKING`` guard:

    from __future__ import annotations
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from plugins.oc_flow._flow_api import agent, parallel, pipeline, phase, log, result, args

The signatures below document the runtime contract exactly.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

#: The value passed via ``--args`` / ``args=`` (``None`` when not provided).
args: Any = None
flow_args: Any = None


def agent(
    prompt: str,
    *,
    label: str = "",
    phase: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    schema: Optional[Dict[str, Any]] = None,
    max_iterations: Optional[int] = None,
    cwd: Optional[str] = None,
    worktree: bool = False,
) -> Any:
    """Run one subagent and return its result.

    Without ``schema`` the return value is the subagent's final text. With
    ``schema`` (a JSON Schema) the subagent is instructed to emit JSON and the
    parsed object is returned. Raises on subagent failure (filter in
    ``parallel``/``pipeline`` if you'd rather drop failures to ``None``).

    Set ``worktree=True`` to run this subagent in its own isolated git worktree
    (so several agents editing files in ``parallel()`` don't collide); the
    worktree is auto-removed afterward if it has no changes.
    """
    raise NotImplementedError("injected at runtime")


def parallel(thunks: List[Callable[[], Any]]) -> List[Any]:
    """Run zero-arg callables concurrently; return results in order.

    A thunk that raises resolves to ``None`` (filter before use).
    """
    raise NotImplementedError("injected at runtime")


def pipeline(items: List[Any], *stages: Callable[..., Any]) -> List[Any]:
    """Run each item through all stages independently — no barrier between
    stages. Each stage receives ``(prev_result, original_item, index)`` (extra
    args optional). A stage that raises drops that item to ``None``.
    """
    raise NotImplementedError("injected at runtime")


def phase(title: str) -> None:
    """Open a new progress phase; later agents are grouped under it."""
    raise NotImplementedError("injected at runtime")


def log(message: str) -> None:
    """Emit a progress line (persisted and shown live)."""
    raise NotImplementedError("injected at runtime")


def result(value: Any) -> None:
    """Set the flow's return value (or assign a top-level ``RESULT = ...``)."""
    raise NotImplementedError("injected at runtime")
