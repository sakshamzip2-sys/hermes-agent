#!/usr/bin/env python3
"""Grade a SOUL.md agent-identity file against the 100-point rubric.

Deterministic pass only: section presence, fail-condition pattern detection, and bloat.
The model still does the judgment pass on each flagged quote. Stdlib only.

Usage:
    python3 grade_soul.py [path-to-SOUL.md]      # default: ~/.hermes/SOUL.md

Exit codes: 0 = clean, 1 = warnings only, 2 = at least one CRITICAL fail condition.
"""
from __future__ import annotations
import os
import re
import sys

# --- Rubric: (key, label, points) -------------------------------------------------
CATEGORIES = [
    ("mission", "Mission", 15),
    ("boundaries", "Role boundaries", 15),
    ("constraints", "Hard constraints", 15),
    ("authority", "Authority & escalation", 15),
    ("truthfulness", "Truthfulness", 14),
    ("artifacts", "Success artifacts", 14),
    ("hygiene", "Runtime hygiene", 12),
]

# Heuristics for "this category is present and substantive". Each is a list of cue
# patterns; a category scores full points if >=1 cue matches a heading or strong line,
# partial if cues appear only weakly.
SECTION_CUES = {
    "mission": [r"\bmission\b", r"\bpurpose\b", r"what (this|i|the) (agent )?(am|is) for", r"\brole\b.*\bis to\b"],
    "boundaries": [r"\bboundaries\b", r"\bscope\b", r"do not\b", r"\bnever\b", r"out of scope", r"\bdoes not\b", r"\brefus"],
    "constraints": [r"\bconstraints?\b", r"\bhard rules?\b", r"\balways\b", r"\bnever\b", r"\bmust not\b"],
    "authority": [r"\bescalat", r"\bapprov", r"\bauthority\b", r"\bpermission", r"\bconfirm\b", r"requires? (a )?human"],
    "truthfulness": [r"\btruthful", r"\bhonest", r"\bdo not (invent|fabricate|make up)", r"\buncertain", r"\bverify\b", r"real (state|output)"],
    "artifacts": [r"\bsuccess\b", r"\bartifact", r"\bdeliverable", r"what (done|success) looks like", r"\bdefinition of done\b"],
    "hygiene": [],  # scored from bloat/secrets, handled below
}

# --- Critical fail-condition detectors -------------------------------------------
SECRET_PATTERNS = [
    (r"(?i)\b(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}", "possible secret literal"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style key"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key block"),
    (r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}", "bearer token literal"),
]

# Side-effecting verbs that need an approval gate nearby.
SIDE_EFFECT_VERBS = r"(publish|post|tweet|send|email|deploy|release|delete|drop|destroy|spend|charge|transfer|wire|pay|push|merge|migrat)"
GATE_WORDS = r"(approv|confirm|escalat|ask|permission|review|human|gate|never without|only after|require)"

FALSE_ACCESS = [
    r"(?i)i (have|'ve got) (full |root |admin )?access to",
    r"(?i)i can (deploy|publish|spend|charge|delete|access) (anything|everything|all)",
    r"(?i)unrestricted access",
]

CONTAMINATION = [
    r"(?i)client [A-Z].*client [A-Z]",  # crude: two named clients
]

BLOAT_LINES = 220          # a constitution past this is probably a runbook
BLOAT_CHARS = 14000
RUNBOOK_CUES = [r"```bash", r"step \d+\b", r"\$ ", r"## Runbook", r"first run.*then run"]


def find_lines(text, pattern):
    out = []
    rx = re.compile(pattern)
    for i, line in enumerate(text.splitlines(), 1):
        if rx.search(line):
            out.append((i, line.strip()))
    return out


def grade(text: str):
    lines = text.splitlines()
    scores = {}
    findings = []        # (severity, line_no, message)
    critical = False

    lowered = text.lower()

    # --- section presence scoring ---
    for key, label, pts in CATEGORIES:
        if key == "hygiene":
            continue
        cues = SECTION_CUES.get(key, [])
        hits = sum(1 for c in cues if re.search(c, lowered))
        if hits >= 2:
            scores[key] = pts
        elif hits == 1:
            scores[key] = round(pts * 0.6)
            findings.append(("warn", None, f"{label}: weakly present (only one cue) — make it an explicit section."))
        else:
            scores[key] = 0
            findings.append(("warn", None, f"{label}: NOT found — add an explicit section."))

    # --- runtime hygiene ---
    hygiene = 12
    nlines, nchars = len(lines), len(text)
    if nlines > BLOAT_LINES or nchars > BLOAT_CHARS:
        hygiene -= 6
        findings.append(("warn", None, f"Bloat: {nlines} lines / {nchars} chars — a constitution should be compact."))
    runbookish = [c for c in RUNBOOK_CUES if re.search(c, text)]
    if len(runbookish) >= 2:
        hygiene -= 4
        critical = True
        findings.append(("critical", None, "Runtime junk: looks like a runbook/log is embedded (move to a skill/doc)."))
    scores["hygiene"] = max(0, hygiene)

    # --- CRITICAL: secrets ---
    for pat, why in SECRET_PATTERNS:
        for ln, content in find_lines(text, pat):
            critical = True
            findings.append(("critical", ln, f"Secret ({why}): {content[:80]}"))

    # --- CRITICAL: false access claims ---
    for pat in FALSE_ACCESS:
        for ln, content in find_lines(text, pat):
            critical = True
            findings.append(("critical", ln, f"False/overbroad access claim: {content[:80]}"))

    # --- CRITICAL: ungated side effects ---
    rx_verb = re.compile(SIDE_EFFECT_VERBS, re.I)
    rx_gate = re.compile(GATE_WORDS, re.I)
    for i, line in enumerate(lines, 1):
        if rx_verb.search(line):
            window = " ".join(lines[max(0, i - 3): i + 2])
            if not rx_gate.search(window):
                critical = True
                findings.append(("critical", i, f"Ungated side effect: {line.strip()[:80]} — no approval/escalation nearby."))

    # --- CRITICAL: contamination ---
    for pat in CONTAMINATION:
        for ln, content in find_lines(text, pat):
            findings.append(("warn", ln, f"Possible cross-client contamination: {content[:80]} (verify)."))

    total = sum(scores.values())
    if critical:
        total = min(total, 60)
    return total, scores, findings, critical


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.hermes/SOUL.md")
    if not os.path.isfile(path):
        print(f"error: SOUL.md not found at {path}", file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as f:
        text = f.read()

    total, scores, findings, critical = grade(text)
    crit_tag = "   [CRITICAL FAIL — capped at 60]" if critical else ""
    print(f"SOUL GRADE: {total}/100{crit_tag}   ({path})")
    for key, label, pts in CATEGORIES:
        print(f"  {label:<22} {scores.get(key, 0):>2}/{pts}")
    crits = [f for f in findings if f[0] == "critical"]
    warns = [f for f in findings if f[0] == "warn"]
    if crits:
        print("\nCRITICAL FINDINGS")
        for _, ln, msg in crits:
            loc = f"line {ln}: " if ln else ""
            print(f"  [CRITICAL] {loc}{msg}")
    if warns:
        print("\nWARNINGS")
        for _, ln, msg in warns:
            loc = f"line {ln}: " if ln else ""
            print(f"  [warn] {loc}{msg}")
    print("\nNext: read each flagged quote IN CONTEXT, confirm/dismiss, and write fixes. "
          "Re-run after edits that grant new tools/memory/cron/posting authority.")
    return 2 if critical else (1 if warns else 0)


if __name__ == "__main__":
    sys.exit(main())
