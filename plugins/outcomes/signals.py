"""Per-turn implicit-signal accumulation + lightweight NL heuristics.

The composite scorer needs cheap, no-LLM signals. ``TurnSignals`` accumulates them
across a turn (fed by ``post_tool_call`` etc.); the detectors read the *next* user
message to infer whether the previous turn earned a correction or an affirmation.

Heuristics favour PRECISION over recall — a false "correction" unfairly tanks the
score, so we only flag clear, unambiguous phrasings. The aux-LLM judge (judge.py)
adds the semantic depth heuristics miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Clear, unambiguous correction phrasings (precision over recall).
# IMPORTANT: the "not X" forms must require a CORRECTIVE object — bare "that's not …"
# falsely matches mild praise like "that's not bad" / "that's not a problem".
_CORRECTION_PATTERNS = (
    r"\bthat'?s\s+(wrong|incorrect|mistaken)\b",           # "that's wrong/incorrect"
    r"\bthat'?s\s+not\s+(right|correct|it|what|how|the\s+right)\b",  # "that's not right/correct/what/how"
    r"\bnot\s+what\s+i\s+(asked|wanted|meant|said|expected)\b",
    r"\byou\s+(misunderstood|got\s+it\s+wrong|misread|missed)\b",
    r"\bactually,?\s+(you|that'?s\s+not|no\b)\b",           # corrective "actually, you/no/that's not"
    r"\bwrong\s+(answer|approach|file|fix|direction)\b",
)

# Clear affirmations.
_AFFIRMATION_PATTERNS = (
    r"\b(perfect|excellent|exactly|awesome|brilliant)\b",
    r"\bgreat\s+(work|job|stuff)\b",
    r"\bthat'?s\s+(right|correct|perfect|it)\b",
    r"\b(thank|thanks)\b.*\b(perfect|great|exactly|works)\b",
    r"\bworks\s+(perfectly|great|now)\b",
)

_CORRECTION_RE = re.compile("|".join(_CORRECTION_PATTERNS), re.IGNORECASE)
_AFFIRMATION_RE = re.compile("|".join(_AFFIRMATION_PATTERNS), re.IGNORECASE)


def detect_correction(user_text: str) -> bool:
    """True only for clear corrective phrasings (precision over recall)."""
    if not user_text or not user_text.strip():
        return False
    return bool(_CORRECTION_RE.search(user_text))


def detect_affirmation(user_text: str) -> bool:
    """True only for clear affirmations."""
    if not user_text or not user_text.strip():
        return False
    return bool(_AFFIRMATION_RE.search(user_text))


@dataclass
class TurnSignals:
    """Mutable accumulator for one turn's implicit signals.

    Fed by hooks during the turn; ``apply_user_followup`` folds in the next user
    message; ``to_score_kwargs`` produces the exact kwargs ``compute_composite_score``
    needs (every key present, so the scorer never KeyErrors).
    """

    tool_success_count: int = 0
    tool_error_count: int = 0
    self_cancel_count: int = 0
    retry_count: int = 0
    conversation_abandoned: bool = False
    affirmation_present: bool = False
    correction_present: bool = False
    vibe_delta: int = 0
    standing_order_violation_count: int = 0

    def record_tool(self, *, success: bool) -> None:
        if success:
            self.tool_success_count += 1
        else:
            self.tool_error_count += 1

    def apply_user_followup(self, user_text: str) -> None:
        """Fold the next user message into correction/affirmation/vibe signals."""
        if detect_correction(user_text):
            self.correction_present = True
            self.vibe_delta = -1
        elif detect_affirmation(user_text):
            self.affirmation_present = True
            self.vibe_delta = 1

    @property
    def tool_call_count(self) -> int:
        return self.tool_success_count + self.tool_error_count

    def to_score_kwargs(self) -> dict:
        return {
            "tool_call_count": self.tool_call_count,
            "tool_success_count": self.tool_success_count,
            "tool_error_count": self.tool_error_count,
            "self_cancel_count": self.self_cancel_count,
            "retry_count": self.retry_count,
            "conversation_abandoned": self.conversation_abandoned,
            "affirmation_present": self.affirmation_present,
            "correction_present": self.correction_present,
            "vibe_delta": self.vibe_delta,
            "standing_order_violation_count": self.standing_order_violation_count,
        }
