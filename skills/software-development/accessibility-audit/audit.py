#!/usr/bin/env python3
"""WCAG 2.2 AA automated checks — the deterministic pass of an accessibility audit.

Catches the violations that are mechanically detectable (missing alt text,
unlabeled form controls, heading-order jumps, missing page language, low text
contrast on inline styles, accessible-name gaps, positive tabindex). Findings
are severity-labeled (blocking / serious / moderate / minor). Exit code gates CI:
2 if any blocking/serious finding, else 0.

Stdlib only (html.parser) — no BeautifulSoup. The model layer of the skill
handles the judgment calls this pass can't (focus order, ARIA correctness,
meaningful alt quality).

Usage:  python audit.py <file.html | dir>   [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from html.parser import HTMLParser
from typing import Dict, List, Optional, Tuple

SEVERITY_ORDER = {"blocking": 0, "serious": 1, "moderate": 2, "minor": 3}


class _A11yParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.findings: List[Dict] = []
        self.heading_levels: List[Tuple[int, int]] = []  # (level, lineno)
        self.html_has_lang = False
        self.saw_html_tag = False
        self._open_label_for: List[str] = []
        self._input_ids_with_label: set = set()
        self._inputs: List[Dict] = []
        self._saw_inline_color = False
        self._label_depth = 0  # >0 while inside a <label> (wraps its control)
        self._has_style_or_link = False  # a <style> block or stylesheet <link>

    def handle_starttag(self, tag, attrs_list):  # noqa: D401 — html.parser signature
        attrs = {k: (v or "") for k, v in attrs_list}
        line = self.getpos()[0]

        if tag == "html":
            self.saw_html_tag = True
            if attrs.get("lang", "").strip():
                self.html_has_lang = True

        if tag == "img":
            if "alt" not in attrs:
                self._add("serious", "1.1.1", line,
                          "<img> has no alt attribute (screen readers can't describe it).")
            elif attrs.get("alt", "").strip() == "" and attrs.get("role") != "presentation":
                # Empty alt is valid ONLY for decorative images; flag as minor to review.
                self._add("minor", "1.1.1", line,
                          "<img> has empty alt — correct only if purely decorative.")

        if tag in ("input", "select", "textarea"):
            itype = attrs.get("type", "text").lower()
            if itype in ("hidden", "submit", "button", "image"):
                pass
            else:
                # A control nested inside <label>…</label> is labeled by wrapping
                # even without a for= — don't false-positive on it.
                self._inputs.append({"attrs": attrs, "line": line,
                                     "wrapped": self._label_depth > 0})

        if tag == "label":
            self._label_depth += 1
            if attrs.get("for"):
                self._input_ids_with_label.add(attrs["for"])

        if tag == "style" or (tag == "link" and "stylesheet" in attrs.get("rel", "").lower()):
            self._has_style_or_link = True

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.heading_levels.append((int(tag[1]), line))

        if "tabindex" in attrs:
            try:
                if int(attrs["tabindex"]) > 0:
                    self._add("moderate", "2.4.3", line,
                              f"positive tabindex={attrs['tabindex']} disrupts natural focus order.")
            except ValueError:
                pass

        if tag == "a" and not attrs.get("href") and "role" not in attrs:
            self._add("minor", "2.1.1", line,
                      "<a> without href is not keyboard-focusable; use a <button>.")

        # Inline-style contrast check (best effort): color + background[-color].
        style = attrs.get("style", "")
        if style:
            self._saw_inline_color = self._saw_inline_color or ("color" in style.lower())
        fg = _css_color(style, "color")
        # Accept both `background-color:` and the `background:` shorthand.
        bg = _css_color(style, "background-color") or _css_color(style, "background")
        if fg and bg:
            ratio = _contrast_ratio(fg, bg)
            if ratio is not None and ratio < 4.5:
                self._add("serious", "1.4.3", line,
                          f"text contrast {ratio:.2f}:1 is below WCAG AA 4.5:1 "
                          f"({_rgb_hex(fg)} on {_rgb_hex(bg)}).")

    def handle_endtag(self, tag):
        if tag == "label" and self._label_depth > 0:
            self._label_depth -= 1

    def _add(self, severity: str, wcag: str, line: int, message: str) -> None:
        self.findings.append({"severity": severity, "wcag": wcag,
                              "line": line, "message": message})

    def finalize(self) -> None:
        # Unlabeled inputs (no for= label, no wrapping <label>, no aria/title).
        for item in self._inputs:
            a = item["attrs"]
            has_name = (item.get("wrapped")
                        or a.get("id") in self._input_ids_with_label
                        or a.get("aria-label", "").strip()
                        or a.get("aria-labelledby", "").strip()
                        or a.get("title", "").strip())
            if not has_name:
                self._add("serious", "1.3.1/4.1.2", item["line"],
                          "form control has no associated label / accessible name.")
        # Page language.
        if self.saw_html_tag and not self.html_has_lang:
            self._add("serious", "3.1.1", 1, "<html> is missing a lang attribute.")
        # Heading order: at most one h1, no skipped levels.
        h1s = [ln for lvl, ln in self.heading_levels if lvl == 1]
        if len(h1s) > 1:
            self._add("moderate", "1.3.1", h1s[1],
                      f"multiple <h1> headings ({len(h1s)}); use one per page.")
        prev = 0
        for lvl, ln in self.heading_levels:
            if prev and lvl > prev + 1:
                self._add("moderate", "1.3.1", ln,
                          f"heading level jumps from h{prev} to h{lvl} (skipped a level).")
            prev = lvl
        # Surface the contrast-coverage limitation so a clean result on a
        # stylesheet-styled page isn't mistaken for "contrast is fine".
        if self._has_style_or_link and not self._saw_inline_color:
            self._add("minor", "1.4.3", 1,
                      "contrast checked on INLINE styles only — this page uses "
                      "<style>/<link> CSS that was not evaluated; verify contrast "
                      "manually or with a rendering-based tool.")


# --- color / contrast helpers (WCAG relative luminance) ---

_NAMED = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0),
          "green": (0, 128, 0), "blue": (0, 0, 255), "gray": (128, 128, 128),
          "grey": (128, 128, 128), "yellow": (255, 255, 0)}


def _css_color(style: str, prop: str) -> Optional[Tuple[int, int, int]]:
    m = re.search(rf"(?<![-\w]){re.escape(prop)}\s*:\s*([^;]+)", style, re.I)
    if not m:
        return None
    value = m.group(1).strip()
    # The `background` shorthand carries more than a color
    # (`background: #fff url(x) no-repeat`). Pull the first color-like token
    # rather than trying to parse the whole value.
    if prop == "background":
        return _first_color_token(value)
    return _parse_color(value)


def _first_color_token(value: str) -> Optional[Tuple[int, int, int]]:
    """Extract the first parseable color from a (possibly multi-part) CSS value."""
    # Try functional colors first (rgb/rgba/hsl spans), then hex, then names.
    for m in re.finditer(r"(rgba?\([^)]*\)|#[0-9a-fA-F]{3,8}|[a-zA-Z]+)", value):
        c = _parse_color(m.group(1))
        if c is not None:
            return c
    return None


def _parse_color(val: str) -> Optional[Tuple[int, int, int]]:
    val = val.strip().lower()
    if val in _NAMED:
        return _NAMED[val]
    m = re.match(r"#([0-9a-f]{3}|[0-9a-f]{6})$", val)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
    m = re.match(r"rgba?\(([^)]+)\)", val)
    if m:
        parts = [p.strip() for p in m.group(1).replace("/", " ").split(",")]
        # Require at least 3 numeric channels — a malformed rgb(1,2) must return
        # None, NEVER a short tuple (which crashed the whole audit when unpacked).
        if len(parts) < 3:
            return None
        try:
            rgb = tuple(int(float(p)) for p in parts[:3])
        except ValueError:
            return None
        # If there's an alpha < ~0.5, the effective contrast is much lower than
        # the opaque colour suggests — skip the (misleading) opaque check rather
        # than report a false PASS.
        if len(parts) >= 4:
            try:
                if float(parts[3]) < 0.5:
                    return None
            except ValueError:
                pass
        return rgb  # type: ignore
    return None


def _rel_lum(rgb: Tuple[int, int, int]) -> float:
    def chan(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(fg: Tuple[int, int, int], bg: Tuple[int, int, int]) -> Optional[float]:
    l1, l2 = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _rgb_hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def audit_html(html: str) -> List[Dict]:
    p = _A11yParser()
    try:
        p.feed(html)
        p.finalize()
    except Exception as exc:  # noqa: BLE001 — one malformed page must not abort
        # the whole run (and bury real findings). Report what we have + a note.
        p.findings.append({"severity": "minor", "wcag": "parse", "line": 0,
                           "message": f"partial audit — parser error: {exc}"})
    return sorted(p.findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["line"]))


def audit_path(path: str) -> Dict[str, List[Dict]]:
    out: Dict[str, List[Dict]] = {}
    if os.path.isdir(path):
        for root, _, files in os.walk(path):
            for fn in files:
                if fn.endswith((".html", ".htm")):
                    fp = os.path.join(root, fn)
                    out[fp] = audit_html(open(fp, encoding="utf-8", errors="replace").read())
    else:
        out[path] = audit_html(open(path, encoding="utf-8", errors="replace").read())
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="WCAG 2.2 AA automated accessibility checks.")
    ap.add_argument("target", help="HTML file or directory.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    results = audit_path(args.target)
    blocking_or_serious = 0
    for findings in results.values():
        blocking_or_serious += sum(1 for f in findings
                                   if f["severity"] in ("blocking", "serious"))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for fp, findings in results.items():
            if not findings:
                print(f"✓ {fp}: no automated violations")
                continue
            print(f"\n{fp}:")
            for f in findings:
                print(f"  [{f['severity']:8}] WCAG {f['wcag']} (line {f['line']}): {f['message']}")
        print(f"\n{blocking_or_serious} blocking/serious finding(s).")

    return 2 if blocking_or_serious else 0


if __name__ == "__main__":
    sys.exit(main())
