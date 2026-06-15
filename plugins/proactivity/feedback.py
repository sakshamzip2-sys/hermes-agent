"""Cadence feedback signals — ported verbatim from OpenComputer v1.

Two pure, deterministic signal sources (no LLM, byte-stable, testable):
  * ``classify_feedback`` reads the user's words ("stop reminding me so much" ->
    tighten; "you never check in" -> loosen; "mute the calendar stuff" -> mute).
  * ``engagement_signal`` reads behaviour (pushed-but-never-acked -> too many) via
    a dead band.
"""

from __future__ import annotations

import re
from typing import Literal, Optional, Union

Feedback = Union[Literal["too_many", "too_few", "none"], tuple[str, str]]

# Order matters: mute (most specific) before the broad too_many/too_few buckets.
_MUTE = re.compile(
    r"(?:mute|stop reminding me about|don'?t remind me about|no more)\s+"
    r"(?:the\s+)?([a-z][a-z0-9_\-]{2,})",
    re.IGNORECASE,
)
_TOO_MANY = re.compile(
    r"too (?:much|many)|stop reminding|remind(?:ing)? me less|less often|"
    r"too many (?:notif|message|reminder)|fewer (?:reminder|message)",
    re.IGNORECASE,
)
_TOO_FEW = re.compile(
    r"never check|check in more|remind me more|more often|don'?t remind me enough|"
    r"wish you'?d check in more",
    re.IGNORECASE,
)

# Stop-words that must not be captured as a mute keyword.
_MUTE_STOP = {"me", "so", "the", "much", "about", "reminding", "stuff", "events", "things"}


def classify_feedback(text: Optional[str]) -> Feedback:
    """Classify a user message into a cadence signal. ``none`` for normal chat."""
    if not text:
        return "none"
    s = str(text)
    m = _MUTE.search(s)
    if m:
        kw = m.group(1).lower()
        if kw not in _MUTE_STOP:
            return ("mute", kw)
    if _TOO_MANY.search(s):
        return "too_many"
    if _TOO_FEW.search(s):
        return "too_few"
    return "none"


def engagement_signal(*, pushed: int, acked: int) -> Literal["too_many", "too_few", "healthy"]:
    """Behavioural signal from delivery outcomes. Dead band [0.3, 0.8] = healthy;
    below = mostly ignored (too many); above = engaged (could do more)."""
    if pushed <= 0:
        return "healthy"
    rate = acked / pushed
    if rate < 0.30:
        return "too_many"
    if rate > 0.80:
        return "too_few"
    return "healthy"
