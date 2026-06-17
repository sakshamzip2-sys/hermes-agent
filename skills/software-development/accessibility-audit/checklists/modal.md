# Modal / Dialog accessibility checklist (WCAG 2.2 AA)

Modals are the most commonly-broken AT pattern. Verify every item.

## Structure
- [ ] Container has `role="dialog"` (or `role="alertdialog"` for urgent confirms).
- [ ] `aria-modal="true"` on the dialog container.
- [ ] Labelled: `aria-labelledby` points at the title element's id, OR
      `aria-label` is set.
- [ ] Described if needed: `aria-describedby` points at the body text.

## Focus management (the part automation can't fully check)
- [ ] On open, focus moves INTO the dialog (the first interactive element, or
      the dialog itself with `tabindex="-1"`).
- [ ] Focus is **trapped**: Tab / Shift+Tab cycle within the dialog only.
- [ ] On close, focus **returns** to the element that opened the dialog.
- [ ] Background content is inert (`inert` attribute or `aria-hidden="true"` on
      the rest of the page) so AT can't reach it.

## Keyboard
- [ ] **Esc** closes the dialog (unless it's a destructive alertdialog that
      requires an explicit choice).
- [ ] All controls operable by keyboard; visible focus indicator on each.

## Common failures this catches
- A `<div onclick>` "X" close button with no `role`/`tabindex` → not keyboard
  reachable. Use a real `<button>`.
- Focus left on the trigger behind the modal → screen-reader users are lost.
- No focus return on close → focus jumps to `<body>` top.
- Background still tabbable → users tab "behind" the modal.
