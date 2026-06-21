# Attribution

The skills in this directory are adapted from Anthropic's financial-services
project and ported into the OpenComputer v2 global skill set so the "Finance
Agent" gallery agent can use them.

- Source: https://github.com/anthropics/financial-services
- License: Apache License 2.0. A full copy of the license text ships alongside
  this file at skills/finance/LICENSE, so the port is self-contained on
  redistribution. The LSEG and S&P Global partner-built skills are also covered
  by the same Apache 2.0 terms in the upstream tree.
- Copyright: Anthropic, PBC and the financial-services contributors. The
  partner-built skills are copyright their respective partners (London Stock
  Exchange Group and S&P Global) under the same Apache 2.0 terms.

## What was taken
The SKILL.md workflows in this directory are adapted from the upstream source
plugins only:

- financial-services/plugins/vertical-plugins/<vertical>/skills/<skill>/
  across the verticals: financial-analysis, equity-research, investment-banking,
  private-equity, wealth-management, fund-admin, operations.
- financial-services/plugins/partner-built/lseg/skills/<skill>/
- financial-services/plugins/partner-built/spglobal/skills/<skill>/

The bundled duplicate copies under plugins/agent-plugins/*/skills/ were NOT used
(those are repackaged copies of the same source skills).

Where a source skill shipped supporting files (references/, scripts/, assets/,
templates/, and inline support documents such as report-template.md or
TROUBLESHOOTING.md), the whole skill package was preserved so progressive
disclosure (skill_view with file_path) continues to work.

## Layout and namespacing
All 65 ported skills live in a single flat namespace, one package per directory:

    skills/finance/<skill-name>/SKILL.md

The source skill directory names and the names inside each SKILL.md frontmatter
are unique across every vertical and partner, so no collision-prefixing was
needed. The original frontmatter (name + description) is kept intact so the
model's trigger matching behaves exactly as upstream intended.

## Changes made
- Repackaged as OpenComputer v2 global skills (capability lives at the edges as
  skills, per the v2 idiom).
- Per-skill LICENSE / LICENSE.txt copies were consolidated into one centralized
  attribution plus a single skills/finance/LICENSE (the full Apache 2.0 text);
  the upstream Apache 2.0 terms still govern.
- Em dashes were removed from every file to comply with the project style rule.
  The substitution preserves meaning (spaced em dashes became commas; bare em
  dashes became hyphens).
- The generic, non-finance meta skill "skill-creator" from the upstream
  financial-analysis vertical was intentionally NOT ported. It is a generic
  skill-authoring helper, not a finance method, and OpenComputer v2 already ships
  equivalent skill-authoring skills (skills-manage, skill-designer). That is the
  only one of the 66 source skills that was skipped.
- The paid, licensed MCP data connectors these workflows reference (FactSet,
  Morningstar, PitchBook, S&P Global, Capital IQ, Refinitiv, Bloomberg,
  Crunchbase, Daloopa, Aiera, LSEG) remain gated off by default and require
  explicit human approval per use. The free public SEC EDGAR source is allowed.
- The "AI drafts, humans sign off" compliance posture from the upstream agents is
  retained as the operating boundary for the Finance Agent.

Apache 2.0 permits this use and redistribution with attribution and a statement
of changes, both provided here.
