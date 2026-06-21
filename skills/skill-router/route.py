#!/usr/bin/env python3
"""Deterministic, lazy skill router.

Picks the single best skill for a natural-language intent using ONLY the
lightweight skill index (name + description from the SKILL.md frontmatter). It
never reads a skill body, so routing stays cheap and context-lean: the chosen
skill is loaded (skill_view/skill_run) only AFTER selection.

The description is the routing signal. Scoring is plain token overlap between the
intent and each description (plus a name-match bonus), tie-broken by the usage
track record (state, success_rate, use_count, recency). This is a thin selector
over existing metadata, not an NLP stack.

Usage:
    python route.py "schedule this report every morning"
Prints a JSON decision: {"decision": route|clarify|none, "chosen": ..., "ranked": [...]}.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_STOPWORDS = {
    "a", "an", "the", "to", "of", "for", "and", "or", "in", "on", "at", "is",
    "are", "do", "does", "did", "i", "me", "my", "you", "your", "it", "that",
    "this", "these", "those", "with", "please", "can", "could", "would", "will",
    "want", "need", "get", "have", "has", "be", "let", "us", "we", "what", "how",
    "from", "into", "by", "as", "so", "if", "then", "now", "just", "some", "any",
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> List[str]:
    return [t for t in _WORD_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 1]


def _index_dirs() -> List[Path]:
    dirs: List[str] = []
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    dirs.append(os.path.join(home, "skills"))
    # Repo seed dir (when run from the repo) so routing works pre-install too.
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "skills"
        if cand.is_dir():
            dirs.append(str(cand))
            break
    seen, out = set(), []
    for d in dirs:
        if d not in seen and Path(d).is_dir():
            seen.add(d)
            out.append(Path(d))
    return out


def _parse_frontmatter(skill_md: Path) -> Optional[Dict[str, str]]:
    """Read ONLY the YAML frontmatter (name + description). Never the body."""
    try:
        with skill_md.open("r", encoding="utf-8") as fh:
            first = fh.readline()
            if first.strip() != "---":
                return None
            name = desc = ""
            for line in fh:  # stops at the closing --- (we break there)
                if line.strip() == "---":
                    break
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
            if not name:
                name = skill_md.parent.name
            return {"name": name, "description": desc}
    except (OSError, UnicodeDecodeError):
        return None


def load_index(extra_index: Optional[List[Dict[str, str]]] = None) -> List[Dict[str, str]]:
    """The lazy index: [{name, description}] for every skill, bodies untouched."""
    if extra_index is not None:
        return extra_index
    out: Dict[str, Dict[str, str]] = {}
    for base in _index_dirs():
        for skill_md in base.rglob("SKILL.md"):
            fm = _parse_frontmatter(skill_md)
            if fm and fm["name"] not in out:
                out[fm["name"]] = fm
    return list(out.values())


def _load_usage() -> Dict[str, Dict[str, Any]]:
    home = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
    path = Path(home) / "skills" / ".usage.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def score_skill(query_tokens: List[str], name: str, description: str) -> float:
    """Token-overlap score of the intent against this skill's description."""
    if not query_tokens:
        return 0.0
    desc_tokens = set(_tokens(description))
    name_tokens = set(_tokens(name.replace("-", " ").replace("_", " ")))
    q = set(query_tokens)
    desc_hits = len(q & desc_tokens)
    name_hits = len(q & name_tokens)
    # Normalize by the intent length so longer phrasings are not over-rewarded;
    # name matches weigh extra because a skill name token in the intent is a
    # strong signal ("cron", "memory", "profile").
    return (desc_hits + 2.0 * name_hits) / max(len(q), 1)


def _analytics_key(rec: Dict[str, Any]) -> Tuple:
    """Tie-break key (higher is better): active state, success rate, uses, recency."""
    state = (rec.get("state") or "active").lower()
    state_rank = {"active": 2, "stale": 1, "archived": 0}.get(state, 2)
    runs = rec.get("run_count") or 0
    ok = rec.get("success_count") or 0
    success_rate = (ok / runs) if runs else 0.5  # unknown -> neutral
    use_count = rec.get("use_count") or 0
    last = rec.get("last_used_at") or rec.get("last_activity_at") or ""
    return (state_rank, round(success_rate, 3), use_count, last)


def route(query: str, k: int = 3, margin: float = 0.15,
          extra_index: Optional[List[Dict[str, str]]] = None,
          usage: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Select the best skill for *query* from descriptions alone.

    decision: "route" (clear winner), "clarify" (top candidates too close),
    or "none" (nothing matches the intent).
    """
    qt = _tokens(query)
    index = load_index(extra_index)
    usage = _load_usage() if usage is None else usage

    scored = []
    for s in index:
        name = s.get("name", "")
        if name in {"skill-router", "intent-dispatcher"}:
            continue  # never route to the router itself
        sc = score_skill(qt, name, s.get("description", ""))
        scored.append({"name": name, "score": round(sc, 4)})

    # Rank by score, then by usage track record for ties.
    def sort_key(item):
        return (item["score"], _analytics_key(usage.get(item["name"], {})))

    scored.sort(key=sort_key, reverse=True)
    ranked = scored[:k]

    if not ranked or ranked[0]["score"] <= 0.0:
        return {"decision": "none", "chosen": None, "ranked": ranked,
                "reason": "No skill description matched the intent."}

    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    if second and (top["score"] - second["score"]) < margin and second["score"] > 0:
        # Too close on description alone: analytics already broke the sort tie,
        # but surface a clarify so a destructive mis-route never happens silently.
        return {"decision": "clarify", "chosen": top["name"], "ranked": ranked,
                "reason": "Multiple skills match closely; confirm which one."}

    return {"decision": "route", "chosen": top["name"], "ranked": ranked,
            "reason": f"Best description match (score {top['score']})."}


def main(argv: List[str]) -> int:
    query = " ".join(argv[1:]).strip()
    if not query:
        print(json.dumps({"decision": "none", "chosen": None, "ranked": [],
                          "reason": "empty intent"}))
        return 0
    print(json.dumps(route(query), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
