"""Example flow: review a set of files across several dimensions, then
adversarially verify each finding.

Run it:
    hermes flow run plugins/oc_flow/examples/review_changes.py \
        --args '["path/to/a.py", "path/to/b.py"]'

Smoke-test the machinery without spending tokens:
    OC_FLOW_FAKE_AGENT=1 hermes flow run plugins/oc_flow/examples/review_changes.py \
        --args '["a.py","b.py"]'

Injected helpers available in this namespace: agent, parallel, pipeline,
phase, log, args, result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # injected at runtime by the engine — imported only for type-checkers
    from plugins.oc_flow._flow_api import agent, args, log, parallel, phase, pipeline, result

META = {
    "name": "review-changes",
    "description": "Review files across dimensions, adversarially verify each finding",
    "phases": ["Review", "Verify"],
}

FINDINGS_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "severity"],
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string"},
                    "file": {"type": "string"},
                },
            },
        }
    },
}

VERDICT_SCHEMA = {
    "type": "object",
    "required": ["is_real", "reason"],
    "properties": {
        "is_real": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

files = args if isinstance(args, list) else ["(no files passed via --args)"]

DIMENSIONS = [
    ("bugs", "logic errors, off-by-one, None handling, race conditions"),
    ("security", "injection, secrets, unsafe deserialization, SSRF, authz"),
    ("perf", "needless work, N+1, blocking calls on hot paths"),
]

phase("Review")
log(f"reviewing {len(files)} file(s) across {len(DIMENSIONS)} dimensions")

reviews = parallel([
    (lambda key=key, desc=desc: agent(
        f"Review these files for {key} issues ({desc}):\n" + "\n".join(files) +
        "\nReturn findings as JSON.",
        label=f"review:{key}", schema=FINDINGS_SCHEMA,
    ))
    for key, desc in DIMENSIONS
])

all_findings = []
for r in reviews:
    if isinstance(r, dict):
        all_findings.extend(r.get("findings", []))
log(f"collected {len(all_findings)} candidate findings")

phase("Verify")
verdicts = pipeline(
    all_findings,
    lambda f: agent(
        f"Adversarially verify this finding — try to REFUTE it. Default to "
        f"is_real=false if uncertain.\nFinding: {f}",
        label="verify", schema=VERDICT_SCHEMA,
    ),
)

confirmed = [
    f for f, v in zip(all_findings, verdicts)
    if isinstance(v, dict) and v.get("is_real")
]
log(f"{len(confirmed)} findings survived verification")

result({"files": files, "candidates": len(all_findings), "confirmed": confirmed})
