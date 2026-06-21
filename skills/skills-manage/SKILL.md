---
name: skills-manage
description: Use when the user wants to inspect or manage an INDIVIDUAL skill (not run it, and not bundles): 'what skills do I have', 'list my skills', 'show me the xlsx skill', 'open the X skill', 'view a skill', 'create a new skill', 'edit this skill', or 'delete a skill'. Deleting a skill or its files is destructive and is confirmed first.
---

# Skills management

Manage individual skills via `skills_list` / `skill_view` / `skill_manage`.

- List / view: `skills_list`, `skill_view(name=...)` (read-only).
- Create / edit: `skill_manage(action="create"|"patch"|"edit", ...)`.
- DELETE (destructive): `skill_manage(action="delete"|"remove_file", ...)`
  removes a skill or its files. Confirm the exact skill first; the system also
  gates deletion via the approval card.
