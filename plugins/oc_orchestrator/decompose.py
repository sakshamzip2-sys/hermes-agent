"""Stage-2 decompose seam: turn a swarm goal into per-profile subtasks.

Stage-1 (router.py) is deterministic triage that picks a lead profile and a set
of candidate profiles when a goal spans multiple domains. Stage-2 takes that
candidate set and slices the goal into one concrete subtask per profile so the
swarm can fan out.

This module is model-agnostic by construction. The actual brain is an INJECTED
callable (``llm``); when none is available it falls back to a deterministic
per-profile slice so decomposition keeps working with no model. Whatever the llm
returns is VALIDATED: entries whose profile is not in the candidate set are
dropped, and the result is hard-capped at ``max_fanout`` (defaulting to the hard
ceiling), so runaway fan-out is impossible regardless of the llm's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .caps import HARD_CEILINGS

# Type of the injected brain: (goal, candidates) -> list of {profile, subtask}.
LlmFn = Callable[[str, List[str]], List[Dict[str, str]]]


@dataclass
class Subtask:
    profile: str
    subtask: str
    rationale: str


def _fallback_subtask(goal: str, profile: str) -> Subtask:
    return Subtask(
        profile=profile,
        subtask=f"As the {profile} specialist, do your part of: {goal}",
        rationale="deterministic per-profile slice (no model available)",
    )


def decompose(
    goal: str,
    candidates: List[str],
    *,
    llm: Optional[LlmFn] = None,
    max_fanout: Optional[int] = None,
) -> List[Subtask]:
    """Slice a swarm goal into per-profile subtasks.

    ``candidates`` is the candidate profile list (e.g. RouteDecision.candidates).
    With ``llm`` provided, its output is validated (out-of-candidates profiles
    dropped) and capped at ``max_fanout``. Without ``llm``, emit one deterministic
    subtask per candidate, capped at ``max_fanout``. Empty candidates -> ``[]``.
    """
    cap = HARD_CEILINGS["max_fanout"] if max_fanout is None else int(max_fanout)
    if cap < 0:
        cap = 0
    if not candidates:
        return []
    allowed = set(candidates)

    if llm is not None:
        raw = llm(goal, list(candidates)) or []
        out: List[Subtask] = []
        for entry in raw:
            if len(out) >= cap:
                break
            # Tolerate a misbehaving llm: a non-dict entry (a bare string, int,
            # tuple, None) is garbage, not a subtask. Drop it rather than crash.
            if not isinstance(entry, dict):
                continue
            profile = entry.get("profile")
            if profile not in allowed:
                continue
            text = entry.get("subtask") or _fallback_subtask(goal, profile).subtask
            out.append(Subtask(profile=profile, subtask=text,
                               rationale="model-proposed subtask (validated)"))
        return out

    return [_fallback_subtask(goal, p) for p in candidates[:cap]]
