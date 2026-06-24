---
title: "Mermaid Maker"
sidebar_label: "Mermaid Maker"
description: "Generate render-ready Mermaid diagrams from natural language, and LINT them against the failure modes LLMs hit most: the reserved word `end`, unquoted labels..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Mermaid Maker

Generate render-ready Mermaid diagrams from natural language, and LINT them against the
failure modes LLMs hit most: the reserved word `end`, unquoted labels with brackets/parens,
raw HTML/angle brackets, stray semicolons, unbalanced brackets, missing diagram-type header,
and oversized diagrams. Use for agent routing maps, memory architecture, tool-permission
flows, multi-agent handoffs, repo architecture, onboarding docs, cron pipelines, product
workflows, and debugging maps. Triggers: "diagram this", "make a mermaid diagram", "map the
system", "show the flow", "flowchart", "sequence diagram", "draw the architecture",
"visualize this workflow". Note: a separate `architecture-diagram` skill exists for SVG —
this one is for Mermaid specifically.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/mermaid-maker` |
| Version | `1.0.0` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Mermaid Maker

A diagram that renders forces the structure to show itself. The value here is **valid syntax +
visible structure**, not decoration. LLM-authored Mermaid breaks in a handful of predictable
ways — generate, then lint, before you hand it over.

## Workflow

1. Pick the diagram type from intent: `flowchart TD` (processes/routing), `sequenceDiagram`
   (handoffs/messages over time), `stateDiagram-v2` (lifecycles), `classDiagram` (data shapes),
   `erDiagram` (schemas).
2. Write the diagram. Keep node IDs simple (`a1`, `svc`, `db`) and put human text in **quoted**
   labels: `n1["User clicks (submit)"]`.
3. **Lint it** before output:
   ```bash
   python3 scripts/mermaid_lint.py diagram.mmd      # or: pipe the diagram on stdin
   ```
   Fix every reported issue and re-lint until clean (exit 0).
4. Output the fenced ```mermaid block + a one-line note on what the diagram reveals. If it has
   >~25 nodes, split into sub-diagrams instead of one unreadable wall.

## Failure modes the linter catches (and how to fix)

- **`end` as a node/id** — lowercase `end` is a flowchart keyword; it silently breaks. Fix:
  capitalize/quote (`End`, `["end"]`).
- **Unquoted special chars in labels** — `(`, `)`, `[`, `]`, `{`, `}`, `:` inside a label
  without quotes. Fix: wrap the label in `"..."`.
- **Raw angle brackets / HTML** — `<thing>` in a label breaks unless it's an allowed `<br>`.
  Fix: quote and use `<br>` only for line breaks.
- **Unbalanced brackets** on a line — a missing `]` or `)`.
- **Missing diagram-type header** — first non-comment line must declare the type
  (`flowchart`, `sequenceDiagram`, ...).
- **Stray trailing semicolons** mixed with newline edges (inconsistent statement separators).
- **Oversized** — too many nodes to render legibly → suggest splitting.

## Rules / pitfalls

- Never emit un-linted Mermaid. The whole point is it renders on the first try.
- Prefer quoted labels everywhere — it's the single biggest source of breakage.
- If you can't diagram the workflow cleanly, you probably don't understand it yet — go back to
  the source, don't ship a vague picture.

## Success criteria

A fenced ```mermaid block that passes `mermaid_lint.py` (exit 0) and renders without manual
fixups, plus a sentence on what structure it exposes.
