# STORM caveats and guardrails

These are built in, not optional. They come from the original Stanford work and the community
notes on the 4-prompt version.

## Grounding limitation (critical)

The real Stanford STORM performs web retrieval to ground every perspective in actual sources.
The pure 4-prompt version simulates the multi-perspective process but skips automatic grounding.
On its own it relies on the model internal knowledge and can produce confident but unverified or
hallucinated content: invented facts, wrong numbers, overconfidence.

## Recommended pairing

For anything you will act on, share, or put in a document, run grounded mode:

- Hand the 5 Key Findings to the `deep-research` skill (nested orchestrator web research) or to
  web search tools, so each claim ties to a real source.
- Run a CitationAgent-style pass: attach each surviving claim to the specific source that
  supports it, so the brief is auditable.
- Or use the official live demo at storm.genie.stanford.edu, or verify by hand.

## Hallucination guardrails

- Always run Prompt 4 (peer review). It is the cheapest defense against confident errors.
- Cross-check every number, named study, and the stated weakest link.
- Treat findings scored under 7 of 10 as unverified.
- A citation proves a claim traces to a source. It does not prove the source is correct or
  authoritative. Prefer primary sources over SEO-optimized secondary content.

## Best for, and not for

- Best for: backstage research, nonfiction, deep dives, decisions, surfacing blind spots.
- Not for: final published content without heavy editing and real sourcing.

## Enhancements supported by this skill

- Auto-generate follow-up questions from the Frontier Question.
- Save each run to the memory bank (`docs/research/storm/`) so research compounds.
- Domain templates (investing, learning, and more) that reweight the perspectives.
- Add a dynamic 6th perspective when Prompt 4 flags a missing angle.
