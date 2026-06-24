---
title: "Kw Pdf Viewer Open — Open a PDF in the interactive viewer"
sidebar_label: "Kw Pdf Viewer Open"
description: "Open a PDF in the interactive viewer"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Pdf Viewer Open

Open a PDF in the interactive viewer

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/pdf-viewer/commands/open` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

> If you need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/pdf-viewer/commands/open/../../CONNECTORS.md).

# Open PDF

Display a PDF document in the live viewer. Use this when the user wants
to **see** a document - not just extract its text.

## Instructions

1. If the user provides a URL or file path, call `display_pdf` with it
2. If no path given, call `list_pdfs` first to show available documents
3. After displaying, offer next steps based on the document type:
   - **Contract / report** → "Want me to highlight key sections or add
     review notes?"
   - **Form** → "This has fillable fields - shall I help you fill it?"
   - **Academic paper** → "Shall I walk through and annotate the key
     findings?"

## Supported Sources

- Local files (paths or drag-and-drop into your working directory)
- arXiv (`arxiv.org/abs/...` auto-converts to PDF URL)
- Any direct HTTPS PDF URL (use the PDF link, not a landing page)

## When NOT to use this

If the user just wants a summary or text extraction, **do not** open
the viewer - use Claude's native Read tool on the PDF path instead.
The viewer is for interactive, visual workflows.
