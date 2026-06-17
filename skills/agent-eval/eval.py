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
    ("ssn", re.compile(r"\b\d{3}[ -]\d{2}[ -]\d{4}\b")),  # dash OR space separated
    # 13-16 digit groups in card-like 4-4-4-4 / spaced / dashed shapes, requiring
    # at least one separator so a bare order-number doesn't false-positive.
    ("credit_card", re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,4}\b")),
    ("phone", re.compile(r"\b\(?\d{3}\)?[ -]?\d{3}[ -]?\d{4}\b")),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
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


_KNOWN_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def _validate_schema(value: Any, schema: Any) -> bool:
    """Minimal JSON-schema validation (type + required + properties).

    Fails CLOSED on a malformed/unknown schema: a typo'd or unsupported ``type``
    must not validate everything (that would let a malformed-output regression
    pass). Use a real jsonschema library for full coverage; this is the
    dependency-free subset.
    """
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    # JSON Schema allows a list of types, e.g. ["string", "null"] — value is
    # valid if it matches ANY of them. (OpenAPI's `nullable: true` is also honored.)
    if isinstance(t, list):
        return any(_validate_schema(value, {**schema, "type": one}) for one in t)
    if value is None and schema.get("nullable") is True:
        return True
    if t == "null":
        return value is None
    if t is not None and t not in _KNOWN_SCHEMA_TYPES:
        return False  # unknown/typo'd type → fail closed
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
    # type omitted entirely → only structural checks above apply; accept.
    return True


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
        # Every called tool must be in the run's available_tools list. If the
        # assertion is DECLARED but the trace omits available_tools, that's
        # insufficient evidence to confirm no tool was hallucinated → FAIL
        # (fail-closed). Silently passing here lets a trace hide the very
        # regression this checks by just not recording its tool list.
        if "available_tools" not in trace:
            return False, "no_hallucinated_tool: trace omits available_tools (cannot verify)"
        available = set(trace.get("available_tools") or [])
        bad = [t for t in tools if t not in available]
        return (not bad), f"no_hallucinated_tool: not-available={bad}"
    return False, f"unknown assertion type: {key}"


def run_eval(cases: List[Dict[str, Any]], traces: List[Dict[str, Any]],
             threshold: float = 1.0) -> Dict[str, Any]:
    if not isinstance(cases, list) or not all(isinstance(c, dict) for c in cases):
        raise ValueError("cases must be a list of objects")
    if not isinstance(traces, list) or not all(isinstance(t, dict) for t in traces):
        raise ValueError("traces must be a list of objects")
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
        assertions = case.get("assertions") or []
        # A case that evaluates ZERO assertions must NOT silently pass — that's
        # how a dropped/typo'd assertions list inflates the gate to green while
        # testing nothing. Treat it as a failed (mis-authored) case.
        if not assertions:
            case_result["error"] = "case has no assertions (nothing verified)"
            case_result["passed"] = False
            results.append(case_result)
            continue
        case_passed = True
        for a in assertions:
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

    try:
        cases = _load_file(args.cases)
        if isinstance(cases, dict):
            cases = cases.get("cases", [])
        traces = _load_file(args.traces)
        if isinstance(traces, dict):
            traces = traces.get("traces", [])
        report = run_eval(cases, traces, args.threshold)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2  # malformed input fails the gate, not a silent pass
    except Exception as exc:  # noqa: BLE001 — never crash CI with a raw traceback
        print(f"error: failed to parse cases/traces: {exc}", file=sys.stderr)
        return 2

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
