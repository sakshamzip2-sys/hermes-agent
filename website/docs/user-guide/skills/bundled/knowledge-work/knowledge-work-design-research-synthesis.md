---
title: "Kw Design Research Synthesis — Synthesize user research into themes, insights, and recommendations"
sidebar_label: "Kw Design Research Synthesis"
description: "Synthesize user research into themes, insights, and recommendations"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Design Research Synthesis

Synthesize user research into themes, insights, and recommendations. Use when you have interview transcripts, survey results, usability test notes, support tickets, or NPS responses that need to be distilled into patterns, user segments, and prioritized next steps.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/design/skills/research-synthesis` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /research-synthesis

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/design/skills/research-synthesis/../../CONNECTORS.md).

Synthesize user research data into actionable insights. See the **user-research** skill for research methods, interview guides, and analysis frameworks.

## Usage

```
/research-synthesis $ARGUMENTS
```

## What I Accept

- Interview transcripts or notes
- Survey results (CSV, pasted data)
- Usability test recordings or notes
- Support tickets or feedback
- NPS/CSAT responses
- App store reviews

## Output

```markdown
## Research Synthesis: [Study Name]
**Method:** [Interviews / Survey / Usability Test] | **Participants:** [X]
**Date:** [Date range] | **Researcher:** [Name]

### Executive Summary
[3-4 sentence overview of key findings]

### Key Themes

#### Theme 1: [Name]
**Prevalence:** [X of Y participants]
**Summary:** [What this theme is about]
**Supporting Evidence:**
- "[Quote]" - P[X]
- "[Quote]" - P[X]
**Implication:** [What this means for the product]

#### Theme 2: [Name]
[Same format]

### Insights → Opportunities

| Insight | Opportunity | Impact | Effort |
|---------|-------------|--------|--------|
| [What we learned] | [What we could do] | High/Med/Low | High/Med/Low |

### User Segments Identified
| Segment | Characteristics | Needs | Size |
|---------|----------------|-------|------|
| [Name] | [Description] | [Key needs] | [Rough %] |

### Recommendations
1. **[High priority]** - [Why, based on which findings]
2. **[Medium priority]** - [Why]
3. **[Lower priority]** - [Why]

### Questions for Further Research
- [What we still don't know]

### Methodology Notes
[How the research was conducted, any limitations or biases to note]
```

## If Connectors Available

If **~~user feedback** is connected:
- Pull support tickets, feature requests, and NPS responses to supplement research data
- Cross-reference themes with real user complaints and requests

If **~~product analytics** is connected:
- Validate qualitative findings with usage data and behavioral metrics
- Quantify the impact of identified pain points

If **~~knowledge base** is connected:
- Search for prior research studies and findings to compare against
- Publish the synthesis to your research repository

## Tips

1. **Include raw quotes** - Direct participant quotes make insights credible and memorable.
2. **Separate observations from interpretations** - "5 of 8 users clicked the wrong button" is an observation. "The button placement is confusing" is an interpretation.
3. **Quantify where possible** - "Most users" is vague. "7 of 10 users" is specific.
