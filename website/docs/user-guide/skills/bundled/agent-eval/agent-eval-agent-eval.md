---
title: "Agent Eval"
sidebar_label: "Agent Eval"
description: "Pin your agent's OWN behavior with a regression suite and gate releases on it — distinct from model benchmarks (MMLU/HumanEval)"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Agent Eval

Pin your agent's OWN behavior with a regression suite and gate releases on it — distinct from model benchmarks (MMLU/HumanEval). Use when a prompt, model, or tool change might silently regress behavior, when you want to assert 'the agent calls the right tool / leaks no PII / returns valid JSON / never invents a tool', when wiring agent-quality checks into CI, or when the user asks to eval/regression-test/score the agent (not the underlying model). Scores recorded run traces against per-case assertions and exits non-zero below a threshold.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/agent-eval` |
| Version | `1.0.0` |
| Platforms | linux, macos, windows |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Agent Behavior Regression Harness

`evaluating-llms-harness` scores the underlying *model* (MMLU, HumanEval). This
skill scores *your agent's behavior* — the tools it picks, the shape of its
output, what it leaks — so a prompt/model/tool change can't silently regress it.

## When to use

- Before/after changing the system prompt, switching models, or adding/removing a tool.
- When the user asks to "eval the agent", "regression-test behavior", "add a CI quality gate".
- To assert non-negotiables: right tool called, no PII in output, output is valid
  JSON against a schema, no hallucinated (non-existent) tool was called.

NOT for: benchmarking the base model (use `evaluating-llms-harness`).

## How it works

1. **Cases** (`cases/*.yaml`) declare a prompt + assertions over the run.
2. **Traces** (JSON) are what real agent runs produced — tool calls + final
   output + the available-tool list:
   ```json
   {"traces": [
     {"case": "web_question_searches_first",
      "tool_calls": [{"name": "web_search", "args": {"query": "mars rover"}}],
      "output": "Today's update ...",
      "available_tools": ["web_search", "terminal", "patch"]}
   ]}
   ```
3. **Score + gate**: the harness checks each assertion and exits non-zero when
   the case pass-rate drops below `--threshold` — fail the build on a regression.

## Run

```bash
python eval.py --cases cases/starter.yaml --traces runs.json --threshold 0.9
echo "exit $?"   # 0 = gate passed, 1 = regression
python eval.py --cases cases/starter.yaml --traces runs.json --json   # machine-readable
```

## Assertions

| Assertion | Passes when |
|-----------|-------------|
| `tool_called: <name>` | a tool with that name appears in the trace |
| `tool_not_called: <name>` | it does not |
| `output_contains: <str>` / `output_not_contains: <str>` | substring (not) present |
| `output_matches_regex: <re>` | regex matches the output |
| `no_pii` | no email / SSN / card / phone in the output |
| `output_valid_json` | output parses as JSON |
| `output_json_schema: <schema>` | parsed output matches a minimal JSON Schema |
| `no_hallucinated_tool` | every called tool is in `available_tools` |

## Capturing traces

Emit a trace per run from your harness/gateway: record each `tool_calls` entry
(name + args), the final `output`, and the `available_tools` list (so
"right tool?" and "no hallucinated tool?" are scorable). Aggregate runs into one
`{"traces": [...]}` file and feed it to `eval.py`.

## CI wiring

Add a job that runs the agent over the case prompts, writes `runs.json`, then:
```bash
python skills/agent-eval/eval.py --cases skills/agent-eval/cases/starter.yaml \
  --traces runs.json --threshold 0.9 || exit 1
```
Use a **different model to judge than the one in production** for any
LLM-as-judge assertions you add, and aggregate over a dataset — per-trajectory
scores are noisy.
