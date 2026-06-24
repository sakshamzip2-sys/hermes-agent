---
title: "Bundles Manage"
sidebar_label: "Bundles Manage"
description: "Use when the user asks specifically about BUNDLES (a saved group of capabilities under one slash command): 'what bundles do I have', 'list my bundles', 'show..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Bundles Manage

Use when the user asks specifically about BUNDLES (a saved group of capabilities under one slash command): 'what bundles do I have', 'list my bundles', 'show the research bundle', or 'run a bundle'. Only for bundles; to view or manage an individual skill, use the skills manager instead.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/bundles-manage` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Skill bundles

A bundle groups several capabilities under one slash command (YAML in
~/.hermes/skill-bundles/). List and show bundles (read-only). To run a bundle,
invoke its slash command. Creating/deleting a bundle edits those YAML files;
deletion is destructive and is confirmed first.
