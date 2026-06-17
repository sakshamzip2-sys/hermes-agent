---
name: accessibility-audit
description: "Review and fix a UI for WCAG 2.2 AA accessibility — semantic HTML, ARIA roles/states, keyboard operability and focus order, colour contrast, focus traps, alt text, form labels. Use when the user asks to make a page/component accessible, run an a11y or WCAG audit, fix screen-reader / keyboard-navigation issues, check colour contrast, or add ARIA. Distinct from visual-design skills (frontend-design/canvas-design): this is about operability for assistive tech, not aesthetics. Runs automated checks (contrast math, missing alt/labels, heading order) then component-level review."
version: 1.0.0
platforms: [linux, macos, windows]
metadata:
  opencomputer:
    tags: [accessibility, a11y, wcag, aria, keyboard, contrast, screen-reader, audit]
    related_skills: [frontend-design, frontend-ui-engineering]
---

# Accessibility Audit (WCAG 2.2 AA)

AI-generated UI is inaccessible by default — missing alt text, unlabeled inputs,
div-buttons that aren't keyboard-operable, low contrast. This skill audits and
fixes a UI against **WCAG 2.2 Level AA**, the practical conformance bar.

## When to use

- "Make this accessible", "run an a11y / WCAG audit", "fix screen-reader issues",
  "is this keyboard-navigable", "check the colour contrast", "add ARIA".
- After building or before shipping any user-facing UI.

Not for visual aesthetics (use `frontend-design`) — this is about *operability*
for assistive technology.

## Four-phase workflow

1. **Scan** — run the automated checker on the target file/dir:
   ```bash
   python audit.py path/to/page.html        # or a directory; --json for machine output
   ```
   It catches the mechanical violations: missing alt, unlabeled controls,
   heading-order jumps, missing `lang`, low inline-style contrast, positive
   tabindex, non-focusable links. Exit code 2 = blocking/serious findings.

2. **Severity-label** every finding:
   - **blocking** — makes content unusable with AT (no keyboard path, no
     accessible name on a control, focus trap with no escape).
   - **serious** — a real barrier (missing alt on meaningful image, contrast
     below 4.5:1, missing page language).
   - **moderate** — degrades the experience (heading skips, redundant ARIA).
   - **minor** — polish (empty alt on a decorative image to confirm).

3. **Fix as diff** — propose minimal edits (route file writes through the
   permission gate). Prefer **semantic HTML first** (`<button>`, `<nav>`,
   `<label for>`, `<main>`) and reach for ARIA only when HTML can't express it
   ("no ARIA is better than bad ARIA"). For component patterns, load the
   relevant checklist under `checklists/`.

4. **Verify** — re-run `audit.py`; confirm zero blocking/serious findings, then
   manually verify the things automation can't: logical focus order, visible
   focus indicator, screen-reader announcement, and that every interactive
   element is reachable and operable by keyboard alone.

Read-only by default — only write fixes when asked.

## Component checklists (progressive disclosure)

Load the one that matches what you're auditing:
- `checklists/forms.md` — labels, errors, required, autocomplete, fieldsets.
- `checklists/modal.md` — focus trap + restore, `role=dialog`, Esc, `aria-modal`.
- `checklists/nav.md` — landmarks, skip link, current-page state, menu keyboard.
- `checklists/tables.md` — `<th scope>`, captions, header association.
- `checklists/media.md` — captions, transcripts, audio control, motion.

## What automation can't check (always do manually)

Meaningful alt-text *quality*, logical reading/focus order, ARIA state
correctness, focus-visible styling, colour as the *only* information channel,
and actual screen-reader behaviour. The automated pass narrows the search; the
judgment is yours.
