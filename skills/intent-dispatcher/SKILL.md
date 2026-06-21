---
name: intent-dispatcher
description: Use when the user expresses an intent in plain language that maps to an existing OpenComputer skill, command, or management action rather than naming it directly. Triggers include scheduling and automation ("schedule this every morning", "run X daily", "pause that job", "delete that cron"), skill management ("what skills do I have", "pin this skill", "archive the unused ones", "show skill health"), profiles, bundles, curator maintenance, memory, and gateway control, as well as any task an installed skill already covers (the user describes the goal, not the skill name). This skill SELECTS the right existing skill or command and runs it; it never reimplements the work. Do NOT use when the user has already named a specific skill or slash command to run.
---

# Intent dispatcher

You are routing a natural-language intent to the capability that ALREADY exists in
OpenComputer. You do not build new functionality and you do not run a separate NLP
model. You map intent to an existing skill or command, run it, and surface what you
did by name. Hermes already carries the metadata you need.

## How to route

1. Decide the kind of intent:
   - A TASK an installed skill covers (e.g. "screen for VCP setups", "make a deck",
     "research X") -> route to a skill.
   - A MANAGEMENT action over the system (cron, profiles, skills, bundles, curator,
     memory, gateway) -> route to the command surface below.

2. For a TASK:
   - Call `skills_list` to get the catalog of `{name, description, category}`.
   - Pick the single best match by reading the descriptions (they already contain
     "Use when..." trigger prose; there is no separate triggers field).
   - If two or more skills plausibly match, break the tie with usage track record:
     prefer `state: active` over stale/archived, then higher `use_count`, then more
     recent `last_used_at`. Read this from the skill-usage data (the gateway exposes
     it at `GET /v1/skills/usage`; the same record lives in
     `~/.hermes/skills/.usage.json`). This is a usage track record, not a success
     rate. If still tied, state the top two and ask the user which they meant.
   - Run it with `skill_run(name="<chosen>")`. Let the skill's own frontmatter decide
     inline vs forked execution.
   - For a deterministic pick you may run `python skills/skill-router/route.py "<intent>"`:
     it selects the best skill from descriptions alone and returns a
     `requires_confirmation` flag (true when the chosen skill is destructive-capable and
     the intent uses a destructive verb). When that flag is true, treat the action as
     gated and do the explicit confirmation in "Destructive actions are gated" below
     before running.

3. For a MANAGEMENT action, map to the verb. Read-only and easily reversible verbs
   run autonomously; destructive verbs are gated (see below).
   - cron: list / add / create / edit / pause / resume / run (autonomous) ;
     **remove** (gated). Prefer the `cronjob` tool for create/list/pause/resume; run
     destructive removal via the terminal CLI so the system approval gate also fires.
   - skills: `skills_list`, `skill_view`, pin/unpin via curator (autonomous) ;
     `skill_manage(action="delete")` and `remove_file` (gated), archive (gated).
   - bundles: list / show (autonomous) ; delete a bundle (gated).
   - profiles: `hermes profile list` (autonomous) ; `hermes profile delete` /
     install / update (gated).
   - curator: `hermes curator status` / `list-archived` (autonomous) ;
     `curator run` / `prune` / `archive` (gated).
   - memory: recall / read (autonomous) ; `memory(action="remove")` and
     `hermes memory reset` (gated).
   - gateway: status (autonomous) ; restart / reload-mcp (gated, it interrupts).

## Destructive actions are gated, always

NEVER silently run a destructive or consequential action. Destructive = deletes,
prunes, archives, overwrites, resets, restarts, or anything that spends money or
sends a message to a third party. The list above marks them.

For a gated action you MUST, before doing it:
1. State exactly what will happen, naming the precise target (which cron id, which
   profile, which skill), and that it cannot be easily undone.
2. Wait for the user's explicit confirmation in their next message. Do not proceed
   on a maybe. If they decline or go quiet, do nothing.
3. The system approval card is a hard backstop and now fires on BOTH paths:
   destructive terminal commands AND the direct tool calls are gated by
   permissions.ask, so `cronjob(action="remove")`, `memory(action="remove")`, and
   `skill_manage(action="delete")` raise the approval card via check_tool_approval
   in gateway context. Use whichever path; the card fires either way. It is a
   backstop, not a substitute for the explicit confirmation in step 1.

Read-only and easily reversible actions (list, view, status, pause, resume) run
autonomously; just do them and report the result.

## Surfacing

Run the chosen skill/command as a normal tool call so it shows up as its own named
step in the timeline. The user should see WHICH skill or command ran, its inputs,
and its result, without seeing the raw skill body. Name the capability you used in
your reply (for example: "Ran skill: vcp-screener" or "Paused cron job morning-brief").
Keep it simple: the name and the outcome, not the machinery.
