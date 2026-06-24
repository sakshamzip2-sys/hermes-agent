---
title: "Browser Control"
sidebar_label: "Browser Control"
description: "Use when the user wants to drive a web browser: 'open this URL', 'go to that website', 'click the button', 'fill the form', 'take a screenshot of the page', ..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Browser Control

Use when the user wants to drive a web browser: 'open this URL', 'go to that website', 'click the button', 'fill the form', 'take a screenshot of the page', 'scroll down', or 'read what's on the page'. Navigation and reading run autonomously; submitting forms that send data should be confirmed.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/browser-control` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Browser control

Drive the browser via the `browser_*` tools: navigate, snapshot, click, type,
scroll, back, screenshot. Navigation and reading are reversible and run
autonomously. Submitting a form that sends data or spends money is consequential;
confirm before that final action.
