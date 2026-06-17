"""Backend aggregator for the frontend **Self-Evolution** tab (``GET /api/self-evolution``).

Read-only view of the whole flywheel so the dashboard can SHOW the agent learning:

* **outcomes** — recent turn_score trend + mean (from the outcomes store), the SENSE signal.
* **dreaming** — last run counts + recent promotions to MEMORY.md (what got consolidated).
* **review**   — the HMAC review queue (pending promotions awaiting accept/reject).
* **skills**   — agent-synthesized playbooks (the EVOLVE output), counted from the skills dir.

Never raises — a failed section reports ``{enabled: false, error: ...}`` so the dashboard
degrades gracefully. All reads are local (sqlite + files); no network, so it's fast.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("hermes.gateway.self_evolution_aggregator")


def _outcomes_section(hermes_home: Path) -> Dict[str, Any]:
    db = hermes_home / "dreaming" / "outcomes.db"
    if not db.exists():
        return {"enabled": True, "recorded": 0, "recent": [], "mean_recent": None}
    import sqlite3

    conn = sqlite3.connect(str(db))
    try:
        total = conn.execute("SELECT COUNT(*) FROM turn_outcomes").fetchone()[0]
        rows = conn.execute(
            "SELECT turn_score, composite, judge, ts FROM turn_outcomes "
            "ORDER BY id DESC LIMIT 50"
        ).fetchall()
    except sqlite3.Error as exc:
        return {"enabled": False, "error": f"outcomes read failed: {exc}"}
    finally:
        conn.close()
    recent = [
        {"turn_score": r[0], "composite": r[1], "judge": r[2], "ts": r[3]}
        for r in rows
    ]
    scores = [r[0] for r in rows]
    mean_recent = sum(scores) / len(scores) if scores else None
    # Oldest-first for a left-to-right trend line on the frontend.
    return {
        "enabled": True,
        "recorded": int(total),
        "mean_recent": mean_recent,
        "trend": list(reversed(scores)),
        "recent": recent,
    }


def _dreaming_section(hermes_home: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"enabled": True, "last_run": None, "last_counts": {}, "recent_promotions": []}
    db = hermes_home / "dreaming" / "dreaming.db"
    if db.exists():
        import sqlite3

        conn = sqlite3.connect(str(db))
        try:
            row = conn.execute(
                "SELECT promoted, updated, held, dropped, evaluated, ts FROM runs "
                "ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            if row:
                out["last_counts"] = {
                    "promoted": row[0], "updated": row[1], "held": row[2],
                    "dropped": row[3], "evaluated": row[4],
                }
                out["last_run"] = row[5]
        except sqlite3.Error:
            pass  # schema may differ; counts stay empty
        finally:
            conn.close()
    # Recent dreamed promotions straight from MEMORY.md (the durable surface).
    mem = hermes_home / "memories" / "MEMORY.md"
    if mem.exists():
        text = mem.read_text(encoding="utf-8", errors="replace")
        dreamed = [
            e.strip() for e in text.split("\n§\n")
            if e.strip().startswith("(dreamed ")
        ]
        out["recent_promotions"] = dreamed[-10:]
        out["promotion_count"] = len(dreamed)
    return out


def _review_section(hermes_home: Path) -> Dict[str, Any]:
    try:
        from plugins.dreaming import review

        home = hermes_home / "dreaming"
        state = review.load_state(home)
        return {
            "enabled": True,
            "pending": [
                {"id": it.id, "text": it.text, "score": it.score,
                 "recall": it.recall_count, "supersede": bool(it.old_text)}
                for it in state.items
            ],
            "pending_count": len(state.items),
            "chain_ok": review.verify_chain(home) if state.items else True,
            "rollback_count": len(state.rollback_log),
        }
    except Exception as exc:  # noqa: BLE001
        return {"enabled": False, "error": f"review read failed: {exc}"}


def _skills_section(hermes_home: Path) -> Dict[str, Any]:
    """Count + list agent-synthesized playbooks (skills with the synthesized banner)."""
    skills_root = hermes_home / "skills"
    synthesized: List[Dict[str, str]] = []
    if skills_root.is_dir():
        for skill_md in skills_root.rglob("SKILL.md"):
            try:
                head = skill_md.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                continue
            if "Synthesized by the agent" in head:
                name = skill_md.parent.name
                desc = ""
                for line in head.splitlines():
                    if line.startswith("description:"):
                        desc = line.split(":", 1)[1].strip()
                        break
                synthesized.append({"name": name, "description": desc})
    return {"enabled": True, "synthesized": synthesized, "synthesized_count": len(synthesized)}


def build_self_evolution_payload(hermes_home: Path) -> Dict[str, Any]:
    """Build the full ``/api/self-evolution`` payload. Never raises."""
    def _safe(fn, name: str) -> Dict[str, Any]:
        try:
            return fn(hermes_home)
        except Exception as exc:  # noqa: BLE001
            logger.warning("self-evolution %s section failed: %s", name, exc)
            return {"enabled": False, "error": str(exc)}

    return {
        "outcomes": _safe(_outcomes_section, "outcomes"),
        "dreaming": _safe(_dreaming_section, "dreaming"),
        "review": _safe(_review_section, "review"),
        "skills": _safe(_skills_section, "skills"),
    }
