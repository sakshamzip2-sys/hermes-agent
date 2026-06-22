# Attribution

The skills under `skills/legal/` are ported from
[anthropics/claude-for-legal](https://github.com/anthropics/claude-for-legal),
licensed under the Apache License 2.0 (see `LICENSE` in this directory). Copyright
the original authors (Anthropic).

## What was ported

The substantive legal-workflow skills from the 11 first-party practice-area plugins:
ai-governance-legal, commercial-legal, corporate-legal, employment-legal, ip-legal,
law-student, legal-clinic, litigation-legal, privacy-legal, product-legal, and
regulatory-legal. Each plugin's `skills/<name>/` package is copied verbatim into
`skills/legal/<vertical>/<name>/`, including its `references/` support files.

## What was changed and why

1. Each skill's frontmatter `name:` is prefixed with its vertical (for example
   `nda-review` becomes `commercial-legal-nda-review`). The OpenComputer v2 skill
   registry keys a skill by its frontmatter name, and the legal repo reuses skill
   names across verticals (and some clash with existing v2 skills), so prefixing keeps
   every ported skill globally unique and its origin obvious.
2. Practice-profile path relocation. Upstream skills read a practice profile at
   `~/.claude/plugins/config/claude-for-legal/<vertical>/`, a path that never exists in
   OpenComputer v2. Every reference is relocated to
   `~/.hermes/legal-practice-profile/<vertical>/`, a real, user-creatable v2 location,
   so a missing profile is an explicit, handled absence rather than a silent read of an
   impossible path.
3. Safety-gate hardening. The `litigation-legal/demand-draft` skill shipped a
   `--skip-gate` flag that bypassed its pre-draft privilege and waiver checklist. Its
   description is rewritten so the checklist always runs inline; the flag is not honored.
4. Em-dash normalization (project style rule on loaded content).

## What was intentionally not ported

- The `legal-builder-hub` plugin (registry browser, skill installer/uninstaller,
  auto-updater, and so on): marketplace management for the upstream plugin catalog,
  not legal work.
- Each vertical's `customize`, `cold-start-interview`, and `matter-workspace` skills:
  plugin setup machinery that reads/writes config paths which do not exist in v2.
- `external_plugins/` (vendor-maintained) and `managed-agent-cookbooks/`.

These skills are reference work product. Every output is a draft for attorney review,
not legal advice. The reviewing attorney is responsible for the legal positions taken.
