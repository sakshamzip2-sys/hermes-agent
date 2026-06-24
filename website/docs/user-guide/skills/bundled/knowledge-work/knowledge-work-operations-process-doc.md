---
title: "Kw Operations Process Doc — Document a business process - flowcharts, RACI, and SOPs"
sidebar_label: "Kw Operations Process Doc"
description: "Document a business process - flowcharts, RACI, and SOPs"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Kw Operations Process Doc

Document a business process - flowcharts, RACI, and SOPs. Use when formalizing a process that lives in someone's head, building a RACI to clarify who owns what, writing an SOP for a handoff or audit, or capturing the exceptions and edge cases of how work actually gets done.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/knowledge-work/operations/skills/process-doc` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# /process-doc

> If you see unfamiliar placeholders or need to check which tools are connected, see [CONNECTORS.md](https://github.com/NousResearch/hermes-agent/blob/main/skills/knowledge-work/operations/skills/process-doc/../../CONNECTORS.md).

Document a business process as a complete standard operating procedure (SOP).

## Usage

```
/process-doc $ARGUMENTS
```

## How It Works

Walk me through the process - describe it, paste existing docs, or just tell me the name and I'll ask the right questions. I'll produce a complete SOP.

## Output

```markdown
## Process Document: [Process Name]
**Owner:** [Person/Team] | **Last Updated:** [Date] | **Review Cadence:** [Quarterly/Annually]

### Purpose
[Why this process exists and what it accomplishes]

### Scope
[What's included and excluded]

### RACI Matrix
| Step | Responsible | Accountable | Consulted | Informed |
|------|------------|-------------|-----------|----------|
| [Step] | [Who does it] | [Who owns it] | [Who to ask] | [Who to tell] |

### Process Flow
[ASCII flowchart or step-by-step description]

### Detailed Steps

#### Step 1: [Name]
- **Who**: [Role]
- **When**: [Trigger or timing]
- **How**: [Detailed instructions]
- **Output**: [What this step produces]

#### Step 2: [Name]
[Same format]

### Exceptions and Edge Cases
| Scenario | What to Do |
|----------|-----------|
| [Exception] | [How to handle it] |

### Metrics
| Metric | Target | How to Measure |
|--------|--------|----------------|
| [Metric] | [Target] | [Method] |

### Related Documents
- [Link to related process or policy]
```

## If Connectors Available

If **~~knowledge base** is connected:
- Search for existing process documentation to update rather than duplicate
- Publish the completed SOP to your wiki

If **~~project tracker** is connected:
- Link the process to related projects and workflows
- Create tasks for process improvement action items

## Tips

1. **Start messy** - You don't need a perfect description. Tell me how it works today and I'll structure it.
2. **Include the exceptions** - "Usually we do X, but sometimes Y" is the most valuable part to document.
3. **Name the people** - Even if roles change, knowing who does what today helps get the process right.
