"""Commitment source — the highest-value proactive signal.

Extracts commitments the user made in recent conversation ("I'll email Sam Friday",
"remind me to call the bank") and emits a follow-up moment for each. This is the source
a personal agent can do that Google/Apple can't — it owns the chat history.

A cheap regex pre-filter gates the (paid) LLM extraction so we only spend tokens when
the window plausibly contains a commitment.
"""

from __future__ import annotations

import hashlib

from .. import llm
from ..moment import Category, ProactiveMoment
from ..models import Sensitivity
from ..session_reader import recent_user_messages
from .base import PollContext


class CommitmentSource:
    id = "commitment"

    def available(self) -> bool:
        return llm.aux_available()

    async def poll(self, ctx: PollContext) -> list[ProactiveMoment]:
        if not ctx.state_db:
            return []
        since = ctx.now - ctx.recent_window_seconds
        turns = recent_user_messages(ctx.state_db, since_ts=since, limit=60)
        candidate = [t for t in turns if llm.has_commitment_hint(t.content)]
        if not candidate:
            return []
        digest = "\n".join(f"- {t.content}" for t in candidate[-30:])
        commitments = await llm.extract_commitments(digest)
        moments: list[ProactiveMoment] = []
        for c in commitments:
            what = c["what"]
            asked = c.get("asked_reminder", False)
            due = c.get("due", "")
            cid = hashlib.sha256(f"commitment|{what.lower()}".encode()).hexdigest()[:16]
            body = f"You mentioned you'd {_lower_first(what)}"
            if due:
                body += f" ({due})"
            body += ". Want a hand with it, or should I check back?"
            moments.append(
                ProactiveMoment(
                    id=cid,
                    source_id=self.id,
                    category=Category.COMMITMENT,
                    title=what,
                    body=body,
                    reasoning=f"You said this in a recent conversation{(' — and asked to be reminded' if asked else '')}.",
                    trigger_at=ctx.now,
                    expires_at=ctx.now + 14 * 24 * 3600.0,
                    urgency=0.6 if asked else 0.4,
                    # "remind me" is a user-initiated loop (push-eligible); a stated
                    # intention is a told-fact (also push-eligible, lower urgency).
                    sensitivity=Sensitivity.USER_LOOP if asked else Sensitivity.TOLD_FACT,
                    confidence=0.9 if asked else 0.7,
                    dedup_key=cid,
                    suggested_action="offer_help",
                    created_at=ctx.now,
                )
            )
        return moments


def _lower_first(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    # "Email Sam by Friday." -> "email Sam by Friday"
    s = s[0].lower() + s[1:]
    return s.rstrip(".")
