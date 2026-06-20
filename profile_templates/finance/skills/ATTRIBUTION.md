# Attribution

The skills in this directory are adapted from Anthropic's financial-services
project:

- Source: https://github.com/anthropics/financial-services
- License: Apache License 2.0 (a full copy ships with the upstream repository at
  its LICENSE file; the cloned copy in this workspace is at
  financial-services/LICENSE)
- Copyright: Anthropic, PBC and the financial-services contributors.

## What was taken
The SKILL.md workflows under this directory (for example earnings-analysis,
model-update, audit-xls, dcf-model, lbo-model, comps-analysis, 3-statement-model,
ic-memo, nav-tieout) are copied from financial-services/plugins/agent-plugins/*/skills/.

## Changes made
- Repackaged as skills of the OpenComputer Finance profile.
- The paid, licensed MCP data connectors these workflows reference (FactSet,
  Morningstar, PitchBook, S&P Global, Capital IQ, Refinitiv, Bloomberg, Crunchbase,
  Daloopa, Aiera) are gated OFF by default and require explicit human approval per
  use. See ../CONNECTORS.md. The free public SEC EDGAR source is allowed.
- The "AI drafts, humans sign off" compliance pattern from the upstream agents is
  retained as a hard boundary in the profile SOUL.md.

Apache 2.0 permits this use and redistribution with attribution and a statement of
changes, both provided here.
