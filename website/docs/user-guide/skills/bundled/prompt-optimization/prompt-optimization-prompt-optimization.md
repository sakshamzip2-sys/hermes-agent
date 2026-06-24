---
title: "Prompt Optimization — Craft and refine LLM prompts for quality and lower cost"
sidebar_label: "Prompt Optimization"
description: "Craft and refine LLM prompts for quality and lower cost"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Prompt Optimization

Craft and refine LLM prompts for quality and lower cost.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/prompt-optimization` |
| Version | `1.0.0` |
| Platforms | linux, macos, windows |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

<!-- Ported from OpenComputer v1. -->

# Prompt Optimization

Craft and refine LLM prompts, system instructions, and chat templates for better
output quality and lower token cost.

## When to Use

- Designing a new system prompt for an agent or chatbot.
- Cutting tokens on a hot prompt that's expensive.
- Debugging "why does the model keep doing X?" failures.

## Procedure

1. **State the goal in one sentence.** If you can't, the prompt is wrong before you
   write it.
2. **Show, don't tell.** Two examples beat a paragraph of instruction — few-shot wins
   for format, style, and tone.
3. **Order matters.** Put critical instructions at the start AND end (recency bias is
   real); the middle gets ignored on long prompts.
4. **Negative space.** "Do not include X" rarely works — phrase it as "respond using
   only Y" instead.
5. **Token-cost audit.** Count tokens (the provider's tokenizer) and cut anything that
   doesn't move quality.
6. **Eval before ship.** Pick 5–10 representative inputs, diff outputs old vs new
   prompt; unintended quality changes mean roll back.

## Pitfalls

- "Think step by step" is overrated — specify *what* to think about ("first identify
  the user's intent, then…").
- Caching beats re-prompting: if a prompt is reused, cache the prefix.
- If the model ignores a rule, it probably conflicts with another rule — surface the
  contradiction rather than adding more rules.

## Verification

Run the 5–10 representative inputs through the old and new prompt and confirm the new
one is at least as good on every case before adopting it.
