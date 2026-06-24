---
title: "Earnings Preview — Build pre-earnings analysis with estimate models, scenario frameworks, and key metrics to watch"
sidebar_label: "Earnings Preview"
description: "Build pre-earnings analysis with estimate models, scenario frameworks, and key metrics to watch"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Earnings Preview

Build pre-earnings analysis with estimate models, scenario frameworks, and key metrics to watch. Use before a company reports quarterly earnings to prepare positioning notes, set up bull/bear scenarios, and identify what will move the stock. Triggers on "earnings preview", "what to watch for [company] earnings", "pre-earnings", "earnings setup", or "preview Q[X] for [company]".

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/finance/earnings-preview` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Earnings Preview

## Workflow

### Step 1: Gather Context

- Identify the company and reporting quarter
- Pull consensus estimates via web search (revenue, EPS, key segment metrics)
- Find the earnings date and time (pre-market vs. after-hours)
- Review the company's prior quarter earnings call for any guidance or commentary

### Step 2: Key Metrics Framework

Build a "what to watch" framework specific to the company:

**Financial Metrics:**
- Revenue vs. consensus (total and by segment)
- EPS vs. consensus
- Margins (gross, operating, net), expanding or contracting?
- Free cash flow
- Forward guidance vs. consensus

**Operational Metrics** (sector-specific):
- Tech/SaaS: ARR, net retention, RPO, customer count
- Retail: Same-store sales, traffic, basket size
- Industrials: Backlog, book-to-bill, price vs. volume
- Financials: NIM, credit quality, loan growth, fee income
- Healthcare: Scripts, patient volumes, pipeline updates

### Step 3: Scenario Analysis

Build 3 scenarios with stock price implications:

| Scenario | Revenue | EPS | Key Driver | Stock Reaction |
|----------|---------|-----|------------|----------------|
| Bull | | | | |
| Base | | | | |
| Bear | | | | |

For each scenario:
- What would need to happen operationally
- What management commentary would signal this
- Historical context, how has the stock moved on similar prints?

### Step 4: Catalyst Checklist

Identify the 3-5 things that will determine the stock's reaction:

1. [Metric] vs. [consensus/whisper number], why it matters
2. [Guidance item], what the buy-side expects to hear
3. [Narrative shift], any strategic changes, M&A, restructuring

### Step 5: Output

One-page earnings preview with:
- Company, quarter, earnings date
- Consensus estimates table
- Key metrics to watch (ranked by importance)
- Bull/base/bear scenario table
- Catalyst checklist
- Trading setup: recent stock performance, implied move from options

## Important Notes

- Consensus estimates change, always note the source and date of estimates
- "Whisper numbers" from buy-side surveys are often more relevant than published consensus
- Historical earnings reactions help calibrate expectations (search for "[company] earnings reaction history")
- Options-implied move tells you what the market expects, compare to your scenarios
