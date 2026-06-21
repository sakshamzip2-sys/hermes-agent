#!/usr/bin/env python3
"""wikijanitor — conservative review of a memory wiki. PROPOSES, never deletes.

Scans a vault for stale notes, review candidates, possible duplicates, broken wikilinks
(gaps), and writes a dated report under wikijanitor-reports/. Stdlib only.

Usage:
    python3 wikijanitor.py [vault-dir]     # default: $MEMORY_WIKI_DIR or ~/.hermes/memory-wiki
"""
from __future__ import annotations
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime

STALE_DAYS = 45
SHORT_CHARS = 200
WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")


def norm_title(name: str) -> str:
    base = re.sub(r"\.md$", "", os.path.basename(name))
    return re.sub(r"[^a-z0-9]+", " ", base.lower()).strip()


def main():
    vault = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "MEMORY_WIKI_DIR", os.path.expanduser("~/.hermes/memory-wiki"))
    if not os.path.isdir(vault):
        print(f"error: vault not found at {vault}", file=sys.stderr)
        return 2

    md_files = []
    for root, _, files in os.walk(vault):
        if os.path.basename(root) == "wikijanitor-reports":
            continue
        for fn in files:
            if fn.endswith(".md"):
                md_files.append(os.path.join(root, fn))

    now = time.time()
    stale, short, gaps = [], [], []
    by_title = defaultdict(list)
    all_basenames = {os.path.basename(p) for p in md_files}
    all_titles = {norm_title(p) for p in md_files}

    for p in md_files:
        try:
            text = open(p, encoding="utf-8").read()
        except Exception:
            continue
        age_days = (now - os.path.getmtime(p)) / 86400
        rel = os.path.relpath(p, vault)
        if age_days > STALE_DAYS:
            stale.append((rel, int(age_days)))
        stripped = text.strip()
        if len(stripped) < SHORT_CHARS or re.fullmatch(r"[#\s\-*]*TODO[\s\S]*", stripped, re.I):
            short.append((rel, len(stripped)))
        by_title[norm_title(p)].append(rel)
        for m in WIKILINK.findall(text):
            target = m.split("|")[0].strip()
            tgt_base = target if target.endswith(".md") else target + ".md"
            if os.path.basename(tgt_base) not in all_basenames and norm_title(tgt_base) not in all_titles:
                gaps.append((rel, target))

    dups = {t: ps for t, ps in by_title.items() if len(ps) > 1}

    report_dir = os.path.join(vault, "wikijanitor-reports")
    os.makedirs(report_dir, exist_ok=True)
    out_path = os.path.join(report_dir, f"{date.today().isoformat()}.md")
    lines = [f"# wikijanitor report — {datetime.now().isoformat(timespec='minutes')}",
             f"\nVault: `{vault}`  ·  {len(md_files)} notes scanned\n",
             "> Proposals only. Nothing was changed. Review and decide.\n"]

    def section(title, rows, fmt):
        lines.append(f"\n## {title} ({len(rows)})")
        if not rows:
            lines.append("- none")
        else:
            for r in rows:
                lines.append("- " + fmt(r))

    section(f"Stale (> {STALE_DAYS}d) — review or archive", stale, lambda r: f"`{r[0]}` — {r[1]}d old")
    section("Review candidates (very short / TODO-only)", short, lambda r: f"`{r[0]}` — {r[1]} chars")
    section("Possible duplicates (similar titles)", list(dups.items()),
            lambda r: f"**{r[0]}** → " + ", ".join(f"`{x}`" for x in r[1]))
    section("Gaps (broken [[wikilinks]] — no target file)", gaps, lambda r: f"`{r[0]}` → `[[{r[1]}]]`")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wikijanitor: scanned {len(md_files)} notes → {out_path}")
    print(f"  stale={len(stale)} review={len(short)} dup-groups={len(dups)} gaps={len(gaps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
