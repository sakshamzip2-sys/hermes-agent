"""Tests for the agent-eval regression harness (STEP 11).

Self-contained: exercises the assertion engine + gate over synthetic traces, no
live agent or API. The key guarantee: a good run passes the gate; a regressed run
fails it (exit non-zero).
"""

import importlib.util
import json
from pathlib import Path

# Load the skill's eval.py by path (it lives under skills/, not an importable pkg).
_EVAL_PY = Path(__file__).resolve().parents[2] / "skills" / "agent-eval" / "eval.py"
_spec = importlib.util.spec_from_file_location("agent_eval_harness", _EVAL_PY)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


CASES = [
    {"name": "web_q", "assertions": [
        {"tool_called": "web_search"}, "no_pii"]},
    {"name": "json_out", "assertions": [
        "output_valid_json",
        {"output_json_schema": {"type": "object", "required": ["name"],
                                "properties": {"name": {"type": "string"}}}}]},
    {"name": "edit", "assertions": [
        {"tool_called": "patch"}, {"tool_not_called": "terminal"},
        "no_hallucinated_tool"]},
]


def _good_traces():
    return [
        {"case": "web_q", "tool_calls": [{"name": "web_search"}],
         "output": "Latest update on the topic.", "available_tools": ["web_search", "patch"]},
        {"case": "json_out", "tool_calls": [], "output": '{"name": "alice"}',
         "available_tools": ["web_search"]},
        {"case": "edit", "tool_calls": [{"name": "patch"}],
         "output": "done", "available_tools": ["patch", "web_search"]},
    ]


def test_good_run_passes_gate():
    report = ev.run_eval(CASES, _good_traces(), threshold=1.0)
    assert report["gate_passed"] is True
    assert report["case_pass_rate"] == 1.0
    assert all(c["passed"] for c in report["cases"])


def test_regressed_tool_choice_fails():
    """The agent stops calling web_search → the web_q case fails."""
    traces = _good_traces()
    traces[0]["tool_calls"] = []  # regression: no search
    report = ev.run_eval(CASES, traces, threshold=1.0)
    assert report["gate_passed"] is False
    web = next(c for c in report["cases"] if c["name"] == "web_q")
    assert web["passed"] is False


def test_pii_leak_fails():
    traces = _good_traces()
    traces[0]["output"] = "Contact john@example.com for details"  # PII leak
    report = ev.run_eval(CASES, traces, threshold=1.0)
    assert report["gate_passed"] is False


def test_invalid_json_fails():
    traces = _good_traces()
    traces[1]["output"] = "not json"
    report = ev.run_eval(CASES, traces, threshold=1.0)
    assert report["gate_passed"] is False


def test_json_schema_mismatch_fails():
    traces = _good_traces()
    traces[1]["output"] = '{"age": 5}'  # missing required "name"
    report = ev.run_eval(CASES, traces, threshold=1.0)
    json_case = next(c for c in report["cases"] if c["name"] == "json_out")
    assert json_case["passed"] is False


def test_hallucinated_tool_fails():
    traces = _good_traces()
    traces[2]["tool_calls"] = [{"name": "nonexistent_tool"}]
    report = ev.run_eval(CASES, traces, threshold=1.0)
    edit = next(c for c in report["cases"] if c["name"] == "edit")
    assert edit["passed"] is False


def test_threshold_allows_partial_pass():
    """With a 0.6 threshold, 2/3 passing cases still passes the gate."""
    traces = _good_traces()
    traces[0]["tool_calls"] = []  # fail 1 of 3 cases → 0.67 pass rate
    report = ev.run_eval(CASES, traces, threshold=0.6)
    assert report["gate_passed"] is True


def test_missing_trace_fails_case():
    report = ev.run_eval(CASES, _good_traces()[:1], threshold=1.0)  # only web_q trace
    assert report["gate_passed"] is False
    missing = [c for c in report["cases"] if c.get("error")]
    assert missing


def test_cli_exit_codes(tmp_path):
    """main() returns 0 when the gate passes, 1 when it fails."""
    cases_f = tmp_path / "cases.json"
    cases_f.write_text(json.dumps({"cases": CASES}))
    good_f = tmp_path / "good.json"
    good_f.write_text(json.dumps({"traces": _good_traces()}))
    assert ev.main(["--cases", str(cases_f), "--traces", str(good_f), "--threshold", "1.0"]) == 0

    bad = _good_traces()
    bad[0]["tool_calls"] = []
    bad_f = tmp_path / "bad.json"
    bad_f.write_text(json.dumps({"traces": bad}))
    assert ev.main(["--cases", str(cases_f), "--traces", str(bad_f), "--threshold", "1.0"]) == 1


def test_starter_suite_loads():
    """The shipped starter.yaml parses and has cases with assertions."""
    starter = _EVAL_PY.parent / "cases" / "starter.yaml"
    cases = ev._load_file(str(starter))["cases"]
    assert len(cases) >= 3
    assert all("assertions" in c for c in cases)


# --- round-2 red-team fixes: no false passes ---

def test_empty_assertions_case_fails_not_passes():
    """A case with zero assertions must FAIL (not silently inflate the rate)."""
    cases = [{"name": "x", "assertions": []}, {"name": "y"}]  # [] and missing
    traces = [{"case": "x", "output": "anything"}, {"case": "y", "output": "z"}]
    report = ev.run_eval(cases, traces, threshold=1.0)
    assert report["gate_passed"] is False
    assert all(c["passed"] is False for c in report["cases"])


def test_unknown_schema_type_fails_closed():
    """A typo'd json-schema type must reject, not validate everything."""
    assert ev._validate_schema({"x": 1}, {"type": "objekt"}) is False
    assert ev._validate_schema("anything", {"type": "strnig"}) is False
    # null is supported and enforced:
    assert ev._validate_schema(None, {"type": "null"}) is True
    assert ev._validate_schema("x", {"type": "null"}) is False


def test_hallucinated_tool_fails_when_available_tools_omitted():
    """Omitting available_tools must FAIL the check (can't be used to hide it)."""
    cases = [{"name": "x", "assertions": ["no_hallucinated_tool"]}]
    traces = [{"case": "x", "tool_calls": [{"name": "made_up"}], "output": "z"}]  # no available_tools
    report = ev.run_eval(cases, traces, threshold=1.0)
    assert report["gate_passed"] is False


def test_malformed_input_returns_gate_failure_not_crash(tmp_path):
    cases_f = tmp_path / "c.json"
    cases_f.write_text('{"cases": [{"name": "x", "assertions": ["no_pii"]}]}')
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert ev.main(["--cases", str(cases_f), "--traces", str(bad)]) == 2


def test_pii_catches_spaced_ssn_and_ip():
    findings = ev._check_pii("user 123 45 6789 at 192.168.1.1")
    assert findings is not None  # caught (either ssn or ipv4)
    # order number should NOT trip credit_card now:
    assert ev._check_pii("order number 1234567890123") is None
