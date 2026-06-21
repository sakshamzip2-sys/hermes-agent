---
name: skill-router
description: Use this FIRST whenever the user expresses an intent in plain language that likely maps to an existing skill or command but does not name one directly (scheduling/cron, profiles, curator/skill maintenance, memory, bundles, gateway, browser, or any task an installed skill covers). It selects the single best skill by matching the user's words against the lightweight skill descriptions, then runs only that skill. Do NOT use it when the user already named a specific skill or slash command.
---

# Skill router (lazy description matching)

Your only job is to pick which OTHER skill to run, cheaply, then run it. Do not
do the work yourself and do not load every skill.

## How to route

1. Run the deterministic selector against the user's intent (it reads ONLY the
   lightweight skill index, name + description, never any skill body):

       python {skill_dir}/route.py "<the user's intent, verbatim>"

   It prints JSON: `{"decision": "route"|"clarify"|"none", "chosen": "<skill>",
   "ranked": [{name, score}, ...], "reason": "..."}`.

2. Act on the decision:
   - `route`  -> the chosen skill is the match. Load and run ONLY that skill with
     `skill_run(name="<chosen>")`. Do not load the others.
   - `clarify` -> two or more skills matched closely. Name the top candidates
     (from `ranked`) and ask the user which they mean. Do not guess on a close
     call, especially for anything destructive.
   - `none` -> nothing matched. Ask the user to rephrase, or handle it directly
     if it is a plain question.

3. Destructive actions are always gated. If the chosen skill performs a
   destructive or consequential action (delete, prune, archive, reset, restart,
   or anything that spends/sends), state exactly what will happen and confirm
   before doing it. The system also enforces a confirmation card for destructive
   tool calls; that is a backstop, not a substitute for telling the user.

## What to surface

Tell the user which skill you chose and why, briefly: e.g. "Routed to
cron-scheduling (best description match), pausing job morning-brief." Keep it to
the skill name and the outcome, not the machinery. The chosen skill then renders
as its own named step in the timeline.
