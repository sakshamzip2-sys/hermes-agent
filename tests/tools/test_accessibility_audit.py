"""Tests for the accessibility-audit skill's automated checker (STEP 14).

A fixture page with known WCAG violations → each is caught and correctly
severity-labeled; a clean page produces no blocking/serious findings.
"""

import importlib.util
from pathlib import Path

_AUDIT_PY = (Path(__file__).resolve().parents[2] / "skills" / "software-development"
             / "accessibility-audit" / "audit.py")
_spec = importlib.util.spec_from_file_location("a11y_audit", _AUDIT_PY)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


_BAD_PAGE = """<html>
<body>
<h1>Welcome</h1>
<h3>Jumped a level</h3>
<img src="hero.png">
<input type="text" name="email" placeholder="Email">
<p style="color:#999999;background-color:#ffffff;">hard to read</p>
<a>broken link</a>
<button tabindex="3">positive tabindex</button>
</body>
</html>"""

_GOOD_PAGE = """<html lang="en">
<body>
<h1>Welcome</h1>
<h2>Section</h2>
<img src="hero.png" alt="A sunrise over mountains">
<label for="email">Email</label>
<input type="text" id="email" name="email">
<p style="color:#111111;background-color:#ffffff;">easy to read</p>
<a href="/next">continue</a>
</body>
</html>"""


def _by_wcag(findings):
    return {f["wcag"]: f for f in findings}


def test_missing_alt_flagged_serious():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "1.1.1" in f
    assert f["1.1.1"]["severity"] == "serious"


def test_unlabeled_input_flagged():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "1.3.1/4.1.2" in f
    assert f["1.3.1/4.1.2"]["severity"] == "serious"


def test_low_contrast_flagged_with_ratio():
    findings = audit.audit_html(_BAD_PAGE)
    contrast = [f for f in findings if f["wcag"] == "1.4.3"]
    assert contrast
    assert "below WCAG AA" in contrast[0]["message"]


def test_heading_jump_flagged_moderate():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "1.3.1" in f  # heading jump
    assert f["1.3.1"]["severity"] == "moderate"


def test_missing_lang_flagged():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "3.1.1" in f


def test_positive_tabindex_flagged():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "2.4.3" in f


def test_non_focusable_link_flagged():
    f = _by_wcag(audit.audit_html(_BAD_PAGE))
    assert "2.1.1" in f


def test_clean_page_has_no_blocking_or_serious():
    findings = audit.audit_html(_GOOD_PAGE)
    bad = [f for f in findings if f["severity"] in ("blocking", "serious")]
    assert bad == [], f"clean page produced serious findings: {bad}"


def test_contrast_math_is_correct():
    # #767676 on white is exactly ~4.54:1 (passes); #777 on white ~4.48 (fails).
    assert audit._contrast_ratio((255, 255, 255), (0, 0, 0)) == 21.0  # max
    ratio = audit._contrast_ratio((118, 118, 118), (255, 255, 255))
    assert 4.4 < ratio < 4.7


def test_cli_exit_code_gates(tmp_path):
    bad = tmp_path / "bad.html"
    bad.write_text(_BAD_PAGE)
    good = tmp_path / "good.html"
    good.write_text(_GOOD_PAGE)
    assert audit.main([str(bad)]) == 2   # blocking/serious → non-zero
    assert audit.main([str(good)]) == 0  # clean → zero


def test_skill_md_present_and_valid():
    skill = _AUDIT_PY.parent / "SKILL.md"
    text = skill.read_text()
    assert text.startswith("---")
    assert "accessibility-audit" in text
    assert "WCAG 2.2 AA" in text


# --- round-2 red-team fixes: no crashes, no false passes ---

def test_malformed_rgb_does_not_crash_whole_audit():
    """A malformed rgb() must not abort the audit and bury other findings."""
    page = ('<html><body><p style="color:rgb(1,2);background:#fff">x</p>'
            '<img src="a.png"></body></html>')
    findings = audit.audit_html(page)  # must NOT raise
    # The missing-alt on the same page is still reported.
    assert any(f["wcag"] == "1.1.1" for f in findings)


def test_background_shorthand_contrast_caught():
    page = '<html lang="en"><body><p style="color:#aaa;background:#fff">low</p></body></html>'
    findings = audit.audit_html(page)
    assert any(f["wcag"] == "1.4.3" and "below WCAG AA" in f["message"] for f in findings)


def test_rgba_low_alpha_not_false_passed():
    """A near-transparent text colour shouldn't be reported as opaque-PASS."""
    # rgba black at 0.1 alpha over white → effectively invisible; we skip the
    # misleading opaque check (return None) rather than report 21:1 PASS.
    assert audit._parse_color("rgba(0,0,0,0.1)") is None
    assert audit._parse_color("rgb(1,2)") is None  # arity-safe


def test_inline_only_contrast_limitation_surfaced():
    """A stylesheet-styled page gets an informational note, not silent 'clean'."""
    page = ('<html lang="en"><head><style>.t{color:#aaa;background:#fff}</style></head>'
            '<body><p class="t">text</p></body></html>')
    findings = audit.audit_html(page)
    assert any("INLINE styles only" in f["message"] for f in findings)


def test_wrapping_label_not_false_positive():
    """An input wrapped in <label> is labeled — must not flag as unlabeled."""
    page = '<html lang="en"><body><label>Email <input type="text"></label></body></html>'
    findings = audit.audit_html(page)
    assert not any(f["wcag"] == "1.3.1/4.1.2" for f in findings)


def test_contrast_math_correct_boundary():
    # #767676 on white ≈ 4.54 (passes AA); #777 on white ≈ 4.48 (fails).
    assert audit._contrast_ratio((118, 118, 118), (255, 255, 255)) > 4.5
    assert audit._contrast_ratio((119, 119, 119), (255, 255, 255)) < 4.5
