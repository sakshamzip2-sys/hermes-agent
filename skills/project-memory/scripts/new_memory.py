#!/usr/bin/env python3
"""Create a dated project-memory entry: docs/memory/YYYY-MM-DD/<slug>-memory-YYYY-MM-DD.md

Usage:
    python3 new_memory.py "<slug>" [--dir REPO_ROOT] [--date YYYY-MM-DD]

Prints the created path. Refuses to overwrite an existing entry. Stdlib only.
"""
from __future__ import annotations
import argparse
import os
import re
import sys
from datetime import date, datetime

TEMPLATE = """# {title}

_Date: {date} · Slug: {slug}_

## What changed
-

## What was learned
-

## Decisions made (with rationale)
-

## Open decisions
-

## Files touched
-

## Source docs referenced
-

## Verification (what was run + result)
-

## Constraints / gotchas
-

## Next steps
-
"""


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip().lower())
    return re.sub(r"-+", "-", s).strip("-") or "note"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slug", help="short descriptive slug for this memory entry")
    ap.add_argument("--dir", default=".", help="repo root (default: cwd)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("error: --date must be YYYY-MM-DD", file=sys.stderr)
            return 2
        day = args.date
    else:
        day = date.today().isoformat()

    slug = slugify(args.slug)
    out_dir = os.path.join(args.dir, "docs", "memory", day)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{slug}-memory-{day}.md")
    if os.path.exists(out_path):
        print(f"error: entry already exists at {out_path} (memory is append-only; pick a new slug)",
              file=sys.stderr)
        return 1

    title = args.slug.strip().rstrip(".").capitalize()
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(title=title, date=day, slug=slug))
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
