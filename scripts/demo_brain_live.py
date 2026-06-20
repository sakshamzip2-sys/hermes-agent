#!/usr/bin/env python3
"""Live LLM-brain orchestration: a real model routes + decomposes a swarm.

End to end on the live gateway:
  1. a narrow goal routes to a SINGLE profile (deterministic, no model needed),
  2. a parallelizable goal is decomposed by a REAL model into per-profile subtasks,
     validated against the candidate set and hard-capped (runaway-proof),
  3. each subtask is assigned as a real Kanban card to its profile (so it surfaces
     in the cockpit under that profile),
  4. brain-down fallback: with the model forced to error, decomposition still
     yields deterministic per-profile subtasks (orchestration never stalls).

Run with the gateway up:
    .venv/bin/python scripts/demo_brain_live.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PROFILES = ["coder", "atlas", "sage", "ledger", "finance"]


def _hr(t):
    print(f"\n=== {t} ===")


def main() -> int:
    from hermes_cli import kanban_db
    from plugins.oc_orchestrator import brain, kanban_bridge

    llm = brain.gateway_llm(model="claude-sonnet-4-6")

    _hr("1. narrow goal -> single profile (deterministic, no model)")
    s = brain.route_decompose("Fix the failing unit test in the parser", PROFILES, llm=llm)
    print(f"shape={s['shape']} lead={s['lead']}  (expected single/coder)")

    _hr("2. parallelizable goal -> REAL model decomposes into per-profile subtasks")
    goal = "Research the EV battery market and build a financial model of the top maker"
    out = brain.route_decompose(goal, PROFILES, llm=llm, max_fanout=5)
    print(f"shape={out['shape']} lead={out['lead']}")
    for st in out["subtasks"]:
        print(f"  - {st['profile']}: {st['subtask'][:80]}")
    # Validate: every chosen profile is a real candidate, count within cap.
    valid = all(st["profile"] in PROFILES for st in out["subtasks"])
    capped = len(out["subtasks"]) <= 5
    print(f"all profiles valid={valid}  within cap={capped}")

    _hr("3. assign each subtask as a real Kanban card to its profile")
    conn = kanban_db.connect()
    assigned = []
    for st in out["subtasks"]:
        tid = kanban_bridge.assign_card(
            conn, title=st["subtask"][:100], profile=st["profile"], board="default")
        assigned.append((st["profile"], tid))
        print(f"  card {tid} -> {st['profile']}")
    conn.close()

    _hr("4. brain-down fallback: model errors -> deterministic decomposition")
    def boom(_):
        raise RuntimeError("model unavailable")
    fb = brain.route_decompose(goal, PROFILES, llm=boom, max_fanout=5)
    print(f"fallback shape={fb['shape']} subtasks={len(fb['subtasks'])} (deterministic, never stalls)")

    _hr("RESULT")
    ok = (s["shape"] == "single" and out["shape"] == "swarm" and valid and capped
          and len(out["subtasks"]) >= 2 and assigned and len(fb["subtasks"]) >= 1)
    print("PASS: live brain routed + decomposed a real swarm, assigned cards, and falls back when down"
          if ok else "FAIL: live brain orchestration not demonstrated")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
