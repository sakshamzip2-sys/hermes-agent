#!/usr/bin/env python3
"""Agent behavior regression harness — assertion engine + CI gate.

Distinct from the model-benchmark `evaluating-llms-harness` (MMLU etc.): this
pins *your agent's own behavior* so a prompt / model / tool change can't silently
regress it. It scores recorded agent run TRACES against per-case assertions and
exits non-zero when the pass rate drops below a threshold — wire it into CI.

A trace is what one agent run produced:
    {"case": "<name>", "tool_calls": [{"name": "...", "args": {...}}],
     "output": "<final text>", "available_tools": ["terminal", ...]}

A case declares assertions over that trace:
    - name: searches the web before answering
      prompt: "what's the weather in Paris right now?"
      assertions:
        - tool_called: web_search
        - no_pii
        - output_not_contains: "I cannot"

Assertion types: tool_called, tool_not_called, output_contains,
output_not_contains, output_matches_regex, no_pii, output_valid_json,
output_json_schema, no_hallucinated_tool.

Usage:
    python eval.py --cases cases/starter.yaml --traces runs.json [--threshold 0.9]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# PII heuristics (US-centric + email) — enough to catch obvious leaks.
_PII_PATTERNS = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("phone", re.compile(r"\b\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
]


def _load_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def _tool_names(trace: Dict[str, Any]) -> List[str]:
    return [str(tc.get("name", "")) for tc in trace.get("tool_calls", []) if isinstance(tc, dict)]


def _check_pii(text: str) -> Optional[str]:
    for label, pat in _PII_PATTERNS:
        if pat.search(text or ""):
            return label
    return None


def _validate_schema(value: Any, schema: Dict[str, Any]) -> bool:
    """Minimal JSON-schema validation (type + required + properties)."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(value, dict):
            return False
        for req in schema.get("required", []):
            if req not in value:
                return False
        for k, sub in (schema.get("properties") or {}).items():
            if k in value and not _validate_schema(value[k], sub):
                return False
        return True
    if t == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        return all(_validate_schema(v, item_schema) for v in value) if item_schema else True
    if t == "string":
        return isinstance(value, str)
    if t in ("number", "integer"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return True  # unknown type → don't fail


def evaluate_assertion(assertion: Any, trace: Dict[str, Any]) -> Tuple[bool, str]:
    """Return (passed, detail) for one assertion against one trace."""
    output = str(trace.get("output", ""))
    tools = _tool_names(trace)

    # Bare-string assertions (e.g. "no_pii", "output_valid_json").
    if isinstance(assertion, str):
        key, val = assertion, None
    elif isinstance(assertion, dict) and len(assertion) == 1:
        key, val = next(iter(assertion.items()))
    else:
        return False, f"malformed assertion: {assertion!r}"

    if key == "tool_called":
        return (val in tools), f"tool_called({val}): tools={tools}"
    if key == "tool_not_called":
        return (val not in tools), f"tool_not_called({val}): tools={tools}"
    if key == "output_contains":
        return (str(val) in output), f"output_contains({val!r})"
    if key == "output_not_contains":
        return (str(val) not in output), f"output_not_contains({val!r})"
    if key == "output_matches_regex":
        return (re.search(str(val), output) is not None), f"output_matches_regex({val!r})"
    if key == "no_pii":
        leak = _check_pii(output)
        return (leak is None), f"no_pii (found: {leak})" if leak else "no_pii"
    if key == "output_valid_json":
        try:
            json.loads(output)
            return True, "output_valid_json"
        except Exception:
            return False, "output_valid_json: not parseable"
    if key == "output_json_schema":
        try:
            parsed = json.loads(output)
        except Exception:
            return False, "output_json_schema: output not JSON"
        return _validate_schema(parsed, val), "output_json_schema"
    if key == "no_hallucinated_tool":
        # Every called tool must be in the run's available_tools list.
        available = set(trace.get("available_tools", []))
        if not available:
            return True, "no_hallucinated_tool: no tool list in trace (skipped)"
        bad = [t for t in tools if t not in available]
        return (not bad), f"no_hallucinated_tool: not-available={bad}"
    return False, f"unknown assertion type: {key}"


def run_eval(cases: List[Dict[str, Any]], traces: List[Dict[str, Any]],
             threshold: float = 1.0) -> Dict[str, Any]:
    by_name = {t.get("case"): t for t in traces}
    results = []
    total_assertions = passed_assertions = 0
    for case in cases:
        name = case.get("name")
        trace = by_name.get(name)
        case_result: Dict[str, Any] = {"name": name, "assertions": []}
        if trace is None:
            case_result["error"] = "no trace for this case"
            case_result["passed"] = False
            results.append(case_result)
            continue
        case_passed = True
        for a in case.get("assertions", []):
            ok, detail = evaluate_assertion(a, trace)
            total_assertions += 1
            passed_assertions += 1 if ok else 0
            case_passed = case_passed and ok
            case_result["assertions"].append({"passed": ok, "detail": detail})
        case_result["passed"] = case_passed
        results.append(case_result)

    case_pass_rate = (sum(1 for r in results if r["passed"]) / len(results)) if results else 0.0
    assertion_pass_rate = (passed_assertions / total_assertions) if total_assertions else 0.0
    return {
        "cases": results,
        "case_pass_rate": round(case_pass_rate, 4),
        "assertion_pass_rate": round(assertion_pass_rate, 4),
        "threshold": threshold,
        "gate_passed": case_pass_rate >= threshold,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Agent behavior regression harness (CI gate).")
    ap.add_argument("--cases", required=True, help="YAML/JSON eval cases file.")
    ap.add_argument("--traces", required=True, help="JSON file of recorded run traces.")
    ap.add_argument("--threshold", type=float, default=1.0,
                    help="Min case pass-rate to pass the gate (default 1.0).")
    ap.add_argument("--json", action="store_true", help="Emit JSON report.")
    args = ap.parse_args(argv)

    cases = _load_file(args.cases)
    if isinstance(cases, dict):
        cases = cases.get("cases", [])
    traces = _load_file(args.traces)
    if isinstance(traces, dict):
        traces = traces.get("traces", [])

    report = run_eval(cases, traces, args.threshold)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for r in report["cases"]:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"[{mark}] {r['name']}")
            if not r["passed"]:
                for a in r.get("assertions", []):
                    if not a["passed"]:
                        print(f"        ✗ {a['detail']}")
                if r.get("error"):
                    print(f"        ✗ {r['error']}")
        print(f"\ncase pass-rate: {report['case_pass_rate']:.0%} "
              f"(threshold {report['threshold']:.0%}) "
              f"→ {'GATE PASSED' if report['gate_passed'] else 'GATE FAILED'}")

    return 0 if report["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
