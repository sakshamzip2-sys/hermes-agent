#!/usr/bin/env python3
"""Vet an agent skill for malicious patterns using NVIDIA SkillSpector.

Thin, model-agnostic wrapper that locates SkillSpector, runs a (default static)
scan, and prints a concise verdict. Exit code gates pre-install / CI checks:

    0  SAFE / low risk
    2  UNSAFE — a HIGH/CRITICAL finding, or risk score >= 60
    3  SkillSpector not found (prints install instructions)
    4  scan failed to run / produce JSON

Usage:
    python scan.py <path-to-skill-or-dir-or-SKILL.md> [--llm] [--json]

``--llm`` enables SkillSpector's semantic stage (requires SKILLSPECTOR_PROVIDER
+ credentials in the environment — SkillSpector is provider-agnostic, so this
honors whatever model/endpoint you configure; static analysis is the default).
``--json`` prints the raw SkillSpector JSON instead of the summary.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

_CONTROL_CHARS = {c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)} | {0x7F}


def _safe(text: str, limit: int = 800) -> str:
    """Strip control/escape characters before echoing subprocess output to a
    terminal — a malicious skill's path or the scanner's diagnostics could
    otherwise smuggle ANSI escape sequences into the user's terminal."""
    cleaned = "".join(ch for ch in (text or "") if ord(ch) not in _CONTROL_CHARS)
    return cleaned[:limit]


UNSAFE_SEVERITIES = {"HIGH", "CRITICAL"}
# SkillSpector bands score >50 as HIGH → recommendation DO_NOT_INSTALL and its
# own CLI exits non-zero there, so match that threshold (not a looser one).
UNSAFE_SCORE = 51
UNSAFE_RECOMMENDATIONS = {"DO_NOT_INSTALL", "UNSAFE", "DANGER"}


def find_skillspector() -> Optional[List[str]]:
    """Return a command prefix that runs SkillSpector, or None.

    Resolution order: $SKILLSPECTOR_BIN, a sibling SkillSpector/.venv checkout,
    ``skillspector`` on PATH, then a local ``skillspector`` Docker image.
    """
    env_bin = os.environ.get("SKILLSPECTOR_BIN", "").strip()
    if env_bin and Path(env_bin).exists():
        return [env_bin]

    # Sibling checkout: <workspace>/SkillSpector/.venv/bin/skillspector
    # (this file: <workspace>/OpenComputerV2/skills/skill-security-scan/scan.py)
    for parents_up in (3, 4):
        try:
            ws = Path(__file__).resolve().parents[parents_up]
        except IndexError:
            continue
        cand = ws / "SkillSpector" / ".venv" / "bin" / "skillspector"
        if cand.exists():
            return [str(cand)]

    on_path = shutil.which("skillspector")
    if on_path:
        return [on_path]

    if shutil.which("docker"):
        # Best-effort Docker fallback (image must be built: `make docker-build`).
        return None  # handled by caller's install hint — avoid surprise container runs
    return None


def run_scan(cmd_prefix: List[str], target: str, use_llm: bool) -> Optional[dict]:
    cmd = [*cmd_prefix, "scan", target, "--format", "json"]
    if not use_llm:
        cmd.append("--no-llm")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"skill-security-scan: failed to run SkillSpector: {_safe(str(exc))}", file=sys.stderr)
        return None
    out = (proc.stdout or "").strip()
    if not out:
        print(f"skill-security-scan: SkillSpector produced no JSON (exit {proc.returncode}).",
              file=sys.stderr)
        if proc.stderr:
            print(_safe(proc.stderr.strip()), file=sys.stderr)
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # Some versions may emit a leading banner; grab the first JSON object.
        start = out.find("{")
        if start != -1:
            try:
                return json.loads(out[start:])
            except json.JSONDecodeError:
                pass
        print("skill-security-scan: could not parse SkillSpector JSON output.", file=sys.stderr)
        return None


def summarize(report: dict) -> int:
    """Print a verdict for one report; return its gating exit code (0 or 2)."""
    skill = report.get("skill") or {}
    risk = report.get("risk_assessment") or {}
    issues = report.get("issues") or []
    score = risk.get("score")
    severity = (risk.get("severity") or "?").upper()
    recommendation = risk.get("recommendation") or "?"

    name = skill.get("name") or skill.get("source") or "skill"
    worst = {(i.get("severity") or "").upper() for i in issues}
    # Gate primarily on SkillSpector's OWN verdict (recommendation), so the
    # wrapper can never be more permissive than the scanner — then add our own
    # severity/score backstops. This closes the 51-59 / MEDIUM-only slip-through.
    unsafe = (
        str(recommendation).upper() in UNSAFE_RECOMMENDATIONS
        or bool(worst & UNSAFE_SEVERITIES)
        or (isinstance(score, (int, float)) and score >= UNSAFE_SCORE)
    )

    verdict = "UNSAFE — do not install" if unsafe else (
        "CAUTION" if any((i.get("severity") or "").upper() == "MEDIUM" for i in issues) else "SAFE"
    )
    print(f"=== {name} ===")
    print(f"  risk score: {score}   severity: {severity}   recommendation: {recommendation}")
    print(f"  verdict: {verdict}   ({len(issues)} finding(s))")
    # Show findings worst-first.
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    for i in sorted(issues, key=lambda x: order.get((x.get("severity") or "").upper(), 9)):
        loc = i.get("location") or {}
        where = loc.get("file") or "?"
        line = loc.get("start_line")
        where = f"{where}:{line}" if line else where
        print(f"    [{(i.get('severity') or '?'):>8}] {i.get('id','?')} {i.get('pattern','')} "
              f"({i.get('category','')}) @ {where}")
        expl = (i.get("explanation") or "").strip().replace("\n", " ")
        if expl:
            print(f"             {expl[:160]}")
    return 2 if unsafe else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Scan an agent skill for malicious patterns (SkillSpector).")
    ap.add_argument("target", help="Path to a skill dir, a SKILL.md, a dir of skills, a repo URL, or a zip")
    ap.add_argument("--llm", action="store_true", help="Enable SkillSpector's LLM semantic stage")
    ap.add_argument("--json", action="store_true", help="Print raw SkillSpector JSON")
    args = ap.parse_args(argv)

    cmd_prefix = find_skillspector()
    if cmd_prefix is None:
        print(
            "skill-security-scan: SkillSpector not found. Install it (isolated):\n"
            "  git clone https://github.com/NVIDIA/SkillSpector && cd SkillSpector \\\n"
            "    && uv venv .venv && uv pip install --python .venv/bin/python -e .\n"
            "Then re-run, or set SKILLSPECTOR_BIN=/path/to/skillspector "
            "(or `docker build -t skillspector .` and set SKILLSPECTOR_BIN to a docker wrapper).",
            file=sys.stderr,
        )
        return 3

    # Audit a tree of skills: scan each immediate child that is itself a skill.
    target_path = Path(args.target)
    targets = [args.target]
    if target_path.is_dir() and not (target_path / "SKILL.md").exists():
        children = sorted(p for p in target_path.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
        if children:
            targets = [str(p) for p in children]

    worst_exit = 0
    for t in targets:
        report = run_scan(cmd_prefix, t, args.llm)
        if report is None:
            worst_exit = max(worst_exit, 4)
            continue
        if args.json:
            print(json.dumps(report, indent=2))
            continue
        worst_exit = max(worst_exit, summarize(report))
    return worst_exit


if __name__ == "__main__":
    raise SystemExit(main())
