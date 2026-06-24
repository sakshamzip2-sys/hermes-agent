---
title: "Portfolio Monitoring — Track and analyze portfolio company performance against plan"
sidebar_label: "Portfolio Monitoring"
description: "Track and analyze portfolio company performance against plan"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Portfolio Monitoring

Track and analyze portfolio company performance against plan. Ingests monthly/quarterly financial packages (Excel, PDF), extracts KPIs, flags variances to budget, and produces summary dashboards. Use when reviewing portfolio company financials, preparing board materials, or monitoring covenant compliance. Triggers on "review portfolio company", "monthly financials", "how is [company] performing", "covenant check", or "portfolio update".

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/finance/portfolio-monitoring` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Portfolio Monitoring

## Workflow

### Step 1: Ingest Financial Package

- Accept the user's portfolio company financial package (Excel workbook, PDF, or CSV)
- Extract key financials: Revenue, EBITDA, cash balance, debt outstanding, capex, working capital
- Identify the reporting period and compare to prior period and budget/plan

### Step 2: KPI Extraction & Variance Analysis

Key metrics to track (adapt to the company's sector):

**Financial KPIs:**
- Revenue vs. budget ($ and %)
- EBITDA and EBITDA margin vs. budget
- Cash balance and net debt
- Leverage ratio (Net Debt / LTM EBITDA)
- Interest coverage ratio
- Capex vs. budget
- Free cash flow

**Operational KPIs** (ask user or infer from data):
- Customer count / revenue per customer
- Employee headcount / revenue per employee
- Backlog / pipeline
- Churn / retention rates

### Step 3: Flag & Summarize

- **Green**: Within 5% of plan
- **Yellow**: 5-15% below plan, flag for discussion
- **Red**: >15% below plan or covenant breach risk, immediate attention

Output a concise summary:
1. One-paragraph executive summary ("Company X is tracking [ahead/behind/on] plan...")
2. KPI table with actual vs. budget vs. prior period
3. Red/yellow flags with context
4. Covenant compliance status (if applicable)
5. Questions for management

### Step 4: Trend Analysis

If multiple periods are provided:
- Chart key metrics over time (revenue, EBITDA, cash)
- Identify trends, accelerating, decelerating, or stable
- Compare vs. underwriting case

## Important Notes

- Always ask for the budget/plan to compare against if not provided
- Don't assume sector-specific KPIs, ask what matters for this company
- If covenant levels aren't known, ask the user for the credit agreement terms
- Output should be board-ready, concise, factual, no fluff
