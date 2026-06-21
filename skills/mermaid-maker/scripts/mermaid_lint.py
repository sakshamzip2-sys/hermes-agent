#!/usr/bin/env python3
"""mermaid_lint — heuristic linter for the failure modes LLM-authored Mermaid hits.

Not a full parser; a fast pre-flight that catches the common breakages. Stdlib only.

Usage:
    python3 mermaid_lint.py diagram.mmd
    cat diagram.mmd | python3 mermaid_lint.py

Exit codes: 0 = clean, 1 = issues found.
"""
from __future__ import annotations
import re
import sys

DIAGRAM_TYPES = (
    "flowchart", "graph", "sequencediagram", "statediagram", "classdiagram",
    "erdiagram", "gantt", "pie", "journey", "gitgraph", "mindmap", "timeline",
    "quadrantchart", "requirementdiagram", "c4context",
)


def lint(text: str):
    issues = []  # (line_no, severity, message)
    raw_lines = text.splitlines()
    # strip %% comments and fenced ```mermaid wrappers for analysis
    content_lines = []
    for i, line in enumerate(raw_lines, 1):
        s = line.strip()
        if s.startswith("%%") or s.startswith("```"):
            content_lines.append((i, ""))
        else:
            content_lines.append((i, line))

    non_empty = [(i, l) for i, l in content_lines if l.strip()]
    if not non_empty:
        return [(1, "error", "empty diagram")]

    # 1) diagram-type header
    first_no = non_empty[0][1].strip().lower()
    if not any(first_no.startswith(t) for t in DIAGRAM_TYPES):
        issues.append((non_empty[0][0], "error",
                       f"missing/invalid diagram-type header (got '{non_empty[0][1].strip()[:30]}'); "
                       "start with flowchart/sequenceDiagram/stateDiagram-v2/..."))

    node_count = 0
    for i, line in non_empty:
        s = line.strip()

        # 2) reserved word `end` as a bare id/node
        for m in re.finditer(r"(?<![\"\w])end(?![\"\w])", s):
            # allowed inside a quoted label; flag if not within quotes
            before = s[:m.start()]
            if before.count('"') % 2 == 0:
                issues.append((i, "error", "`end` is a reserved word — quote or capitalize it (`End`/`[\"end\"]`)"))
                break

        # 3) unbalanced brackets on the line
        for open_c, close_c in (("[", "]"), ("(", ")"), ("{", "}")):
            if s.count(open_c) != s.count(close_c):
                issues.append((i, "error", f"unbalanced '{open_c}{close_c}' on line"))
                break

        # 4) unquoted labels with special chars (quote-aware: strip quoted labels first,
        #    so a correctly-quoted "label (with parens)" is NOT flagged)
        sans_q = re.sub(r'"(?:[^"\\]|\\.)*"', '', s)
        for m in re.finditer(r"\[([^\[\]]*)\]", sans_q):
            body = m.group(1).strip()
            if body and re.search(r"[()<>{}:]", body):
                issues.append((i, "warn", f'unquoted label "{body[:30]}" has special chars — wrap it in quotes'))

        # 5) raw angle-bracket HTML outside <br> and outside quotes
        if re.search(r"<(?!br\s*/?>)[a-zA-Z/]", sans_q):
            issues.append((i, "warn", "raw HTML/angle bracket outside a quoted label — quote it"))

        # 6) stray trailing semicolon mixed with newline edges
        if s.endswith(";"):
            issues.append((i, "warn", "trailing ';' — be consistent (Mermaid uses newlines as separators)"))

        # crude node tally for size check
        node_count += len(re.findall(r"-->|---|==>|-\.->", s))

    # 7) oversized
    if node_count > 40:
        issues.append((0, "warn", f"~{node_count} edges — likely too large to render legibly; split into sub-diagrams"))

    return issues


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("-", "/dev/stdin"):
        text = open(sys.argv[1], encoding="utf-8").read()
        src = sys.argv[1]
    else:
        text = sys.stdin.read()
        src = "<stdin>"
    issues = lint(text)
    errors = [x for x in issues if x[1] == "error"]
    warns = [x for x in issues if x[1] == "warn"]
    if not issues:
        print(f"mermaid_lint: {src} — clean ✓")
        return 0
    print(f"mermaid_lint: {src} — {len(errors)} error(s), {len(warns)} warning(s)")
    for ln, sev, msg in issues:
        loc = f"line {ln}: " if ln else ""
        print(f"  [{sev}] {loc}{msg}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
