---
name: storm
description: |
  STORM (Synthesis of Topic Outlines through Retrieval and Multi-perspective question asking),
  the Stanford NAACL 2024 research method, run as a 4-prompt workflow. It simulates 5 expert
  perspectives on a topic, maps where they contradict, synthesizes a reliability-ranked
  briefing, then peer reviews itself. Use for deep nonfiction research, big decisions,
  investing, interview prep, learning a new field, negotiation prep, and surfacing blind spots.
  Pairs with the deep-research skill for grounded web retrieval. Triggers: "storm",
  "multi-perspective research", "research this from every angle", "contradiction map",
  "5 perspectives", "peer review my research", "find the blind spots", "phd-level research".
version: 1.0.0
metadata:
  opencomputer:
    tags: [research, storm, multi-perspective, synthesis, deep-research]
    related_skills: [deep-research, competitor-analysis, llm-wiki, research-paper-writing]
---

# STORM: multi-perspective research in 4 prompts

STORM stands for Synthesis of Topic Outlines through Retrieval and Multi-perspective question
asking. Published at NAACL 2024 by the Stanford OVAL Lab. The live tool is at
storm.genie.stanford.edu and the code is at github.com/stanford-oval/storm (MIT). The method is
the prize: ask a topic from 5 expert angles, map where they fight, synthesize, then self
critique. The Stanford paper measured multi-perspective articles as about 25 percent more
organized and 10 percent broader in coverage than single-prompt research.

## Read this first: persona simulation vs grounded research

The pure 4-prompt version is a powerful thinking template, but on its own it runs on the model
internal knowledge and can produce confident but unverified or hallucinated claims (wrong
numbers, invented facts, overconfidence). Two modes:

- **Persona mode** (fast, ungrounded): run the 4 prompts as-is. Label the output clearly as
  persona simulation. Good for backstage thinking, surfacing angles and blind spots.
- **Grounded mode** (preferred for anything you will act on or share): before the synthesis,
  hand each perspective to the `deep-research` skill (or web search tools) so the 5 Key Findings
  are tied to real sources, then run a CitationAgent-style pass that attaches each claim to its
  source. The deep-research agent uses this mode by default.

Always run Prompt 4 (peer review). Always cross-check numbers and the weakest link.

## The 4 prompts

The exact, copy-paste-ready prompts live in `prompts/` with `[YOUR TOPIC]` and `[YOUR ROLE]`
placeholders:

1. `prompts/01-multi-perspective-scan.md` : simulate 5 experts (practitioner, academic,
   skeptic, economist, historian). Core position, strongest evidence, the one thing only they
   would say.
2. `prompts/02-contradiction-map.md` : where the voices clash, strongest vs weakest evidence,
   the question that resolves the biggest conflict, what every voice agrees on (likely true),
   and what none addressed (the field blind spot).
3. `prompts/03-synthesis.md` : one-paragraph CEO summary, 5 key findings ranked by reliability,
   the hidden connection, the actionable insight for `[YOUR ROLE]`, and the frontier question.
4. `prompts/04-peer-review.md` : confidence scores 1 to 10 per finding, the weakest link, a bias
   check, a missing 6th perspective, and an overall Stanford-professor grade.

## The 5-minute workflow

- Minute 1: Prompt 1. Five expert views.
- Minutes 2 to 3: Prompt 2. A contradiction map.
- Minutes 3 to 4: Prompt 3. A research briefing.
- Minute 5: Prompt 4. You know what is reliable and what is not.

Chain the outputs: each prompt reads the prior outputs in the same conversation. In grounded
mode, insert retrieval between Prompt 1 and Prompt 3.

## 7 ways to use it

1. Before writing any article or report (cover angles others miss).
2. Before a major business decision (practitioner reality, skeptic risks, economist incentives).
3. Before a job interview (insider language, sharp questions).
4. Before investing (bull, bear, historical parallel, incentive map, academic evidence). See
   `templates/investing-template.md`.
5. Before learning a new skill (what to learn first, the theory, the overhyped). See
   `templates/learning-template.md`.
6. Before a negotiation (the other side incentives, weaknesses, history).
7. Before any presentation (answer objections before they are raised).

## Memory bank: make it compounding

Save each run so research compounds across sessions. Write the topic, the 5 perspectives, the
contradiction map, the briefing, and the peer review to:

`docs/research/storm/YYYY-MM-DD-<slug>.md`

When a new topic overlaps a past run, read the prior file first and note contradictions or
confirmations against it. The deep-research agent additionally records durable conclusions in
its profile memory so future sessions inherit them.

## Guardrails (always on)

- Always run Prompt 4. Treat any finding under a 7 of 10 confidence as unverified.
- In persona mode, label output as persona simulation, not sourced fact.
- Numbers, named studies, and the weakest link must be cross-checked (grounded mode, web tools,
  or the Stanford live demo) before anyone acts on them.
- Full detail in `caveats-and-guardrails.md`.

## Success criteria

A dated STORM brief (5 perspectives, contradiction map, ranked findings, one specific action,
reliability scores) where, in grounded mode, the key findings carry citations and survived the
peer-review pass.
