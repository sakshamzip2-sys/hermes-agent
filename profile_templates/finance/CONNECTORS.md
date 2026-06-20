# Finance data connectors (gated)

The finance profile's workflows are adapted from Anthropic's financial-services
agents, which call paid, licensed market-data MCP connectors. Those connectors are
OFF by default in OpenComputer and require explicit human approval per use. Wire
the structure; do not enable a paid source without sign-off.

## Paid / licensed (default OFF, require approval)

| Connector  | MCP namespace        | Status   |
|------------|----------------------|----------|
| FactSet    | mcp__factset__*      | gated    |
| Morningstar| mcp__morningstar__*  | gated    |
| PitchBook  | mcp__pitchbook__*    | gated    |
| S&P Global | mcp__spglobal__*     | gated    |
| Capital IQ | mcp__capitaliq__*    | gated    |
| Refinitiv  | mcp__refinitiv__*    | gated    |
| Bloomberg  | mcp__bloomberg__*    | gated    |
| Crunchbase | mcp__crunchbase__*   | gated    |
| Daloopa    | mcp__daloopa__*      | gated    |
| Aiera      | mcp__aiera__*        | gated    |

## Free / public (allowed)

| Source    | Notes                                            |
|-----------|--------------------------------------------------|
| SEC EDGAR | Public filings (10-K, 10-Q, 8-K). No gating.     |

## How gating works

- The profile SOUL.md states the boundary: paid connectors require explicit human
  approval per use, and the agent drafts only (it never publishes or trades).
- No paid connector is registered in this profile's mcp config by default. Enabling
  one is a deliberate, recorded human action (the licensing/credentials step), not
  an agent decision.
- A skill that needs a gated connector must first check approval and, if absent,
  fall back to EDGAR or stop and request sign-off. It never silently proceeds.

This matches OpenComputer's house rule: hard stop before paid or irreversible
actions. Adapted from anthropics/financial-services (Apache License 2.0); see
skills/ATTRIBUTION.md.
