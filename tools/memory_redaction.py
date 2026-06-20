"""Memory-ingest redaction (req #8, MEMORY-POLICY section 3).

``redact(text) -> (redacted_text, hits)`` replaces secrets / credentials / high
sensitivity PII with a TYPED placeholder like ``[REDACTED:aws_key]`` so a fact
stays useful without leaking the secret. It runs on the write path BEFORE every
fact INSERT (the reconcile engine and the inline memory tools), per the locked
MEMORY-POLICY.

Design constraints (from the task + MEMORY-POLICY):
  - Pure stdlib ``re`` only. No third-party deps. Deterministic.
  - TYPED placeholders, not masking. ``[REDACTED:<kind>]`` so the reader sees
    what KIND of secret was removed (and the stored content never contains the
    raw secret substring).
  - Do NOT over-redact: a bare email address or a first name is allowed (it is
    often legitimately part of identity memory). Redaction targets credentials
    and high-sensitivity PII, never all personal mentions.

Covered patterns (MEMORY-POLICY section 3):
  - API keys / tokens: ``sk-...``, ``sk-or-...``, ``ghp_...``/``gho_...`` and the
    other GitHub PAT prefixes, AWS ``AKIA[0-9A-Z]{16}``, Slack ``xoxb-``/``xoxp-``
    (and the other xox* variants), bearer tokens, JWTs (``eyJ....eyJ....``).
  - Private keys: ``-----BEGIN ... PRIVATE KEY-----`` ... ``-----END ... PRIVATE
    KEY-----`` blocks.
  - Connection strings with embedded credentials: ``scheme://user:pass@host``.
  - Passwords in ``key=value`` / ``password: ...`` shapes.
  - Luhn-valid payment-card numbers.

No em dashes (house rule). Pyright-clean.
"""

from __future__ import annotations

import re
from typing import List, Pattern, Tuple

__all__ = [
    "redact",
    "luhn_valid",
    "REDACTION_KINDS",
    "scan_supplementary_injection",
    "SUPPLEMENTARY_INJECTION_KINDS",
]


def _ph(kind: str) -> str:
    """The typed placeholder for a redaction ``kind``."""
    return f"[REDACTED:{kind}]"


# ---------------------------------------------------------------------------
# Supplementary injection-shape detection (P2, defense-in-depth).
#
# The reconcile fence already runs ``scan_for_threats(scope="strict")``, which
# catches the classic ``ignore all previous instructions`` shape. But several
# real injection shapes slipped through and were STORED as trusted facts:
#   - system-role impersonation tags / prefixes: ``<system>...</system>``,
#     ``<|system|>``, a line that STARTS ``SYSTEM:`` / ``Assistant:``;
#   - ``disregard / ignore the above`` (the strict scanner's ``disregard_rules``
#     pattern requires a trailing ``your/all/any instructions``, so the bare
#     ``disregard the above`` form is not caught);
#   - destructive-command imperatives: ``rm -rf /``, ``drop table/database``,
#     ``mkfs``, ``dd if=``, a fork bomb.
#
# This is a SUPPLEMENT to (not a replacement for) ``scan_for_threats``: the
# reconcile path runs both and SKIPs on a hit from either. The patterns are
# deliberately CONSERVATIVE (anchored, word-bounded, line-anchored where a
# generic token would over-match) so curated memory prose ("my system is
# macOS", "we may drop the feature flag", "the system: prompt the user") does
# NOT trip them. Pure stdlib ``re``, deterministic, no third-party deps.
# ---------------------------------------------------------------------------

SUPPLEMENTARY_INJECTION_KINDS = frozenset(
    {
        "role_tag_impersonation",
        "role_prefix_impersonation",
        "disregard_above",
        "destructive_rm",
        "destructive_sql",
        "destructive_cmd",
    }
)

_SUPPLEMENTARY_INJECTION_PATTERNS: List[Tuple[Pattern[str], str]] = [
    # <system>, </system>, <|system|>, <assistant>, <user> role-impersonation
    # tags. Both the bare-angle and the pipe-delimited (``<|system|>``) forms.
    (
        re.compile(r"<\s*/?\s*\|?\s*(?:system|assistant|user)\s*\|?\s*>", re.IGNORECASE),
        "role_tag_impersonation",
    ),
    # A line that STARTS with a system/assistant role prefix (optionally
    # bracketed): ``SYSTEM:``, ``[SYSTEM]:``, ``Assistant:``. Anchored to line
    # start so "the system: ..." or "my system is ..." do NOT match.
    (
        re.compile(
            r"(?m)^\s*[\[\(\{]?\s*(?:system|assistant)\s*[\]\)\}]?\s*:",
            re.IGNORECASE,
        ),
        "role_prefix_impersonation",
    ),
    # "disregard / ignore / forget / override (everything | all | the) above".
    # Complements the strict scanner's instruction-anchored variant.
    (
        re.compile(
            r"\b(?:disregard|ignore|forget|override)\s+"
            r"(?:everything\s+)?(?:the\s+|all\s+(?:the\s+)?)?above\b",
            re.IGNORECASE,
        ),
        "disregard_above",
    ),
    # Destructive ``rm -rf`` / ``rm -fr`` / ``rm -r`` / ``rm -f`` imperatives.
    (re.compile(r"\brm\s+-[a-z]*[rf][a-z]*\b", re.IGNORECASE), "destructive_rm"),
    # Destructive SQL DDL/DML imperatives.
    (
        re.compile(r"\b(?:drop|truncate)\s+(?:table|database|schema)\b", re.IGNORECASE),
        "destructive_sql",
    ),
    # Other classic destructive one-liners: mkfs, raw dd write, fork bomb.
    (
        re.compile(r"\bmkfs\b|\bdd\s+if=|:\s*\(\s*\)\s*\{", re.IGNORECASE),
        "destructive_cmd",
    ),
]


def scan_supplementary_injection(text: str) -> List[str]:
    """Return supplementary injection-shape pattern IDs present in ``text``.

    A conservative defense-in-depth complement to
    ``tools.threat_patterns.scan_for_threats(scope="strict")``. Returns the
    matched KIND strings (a subset of :data:`SUPPLEMENTARY_INJECTION_KINDS`), in
    pattern order, with no duplicates. Empty list = clean (the common case).
    """
    if not text:
        return []
    hits: List[str] = []
    for pattern, kind in _SUPPLEMENTARY_INJECTION_PATTERNS:
        if kind in hits:
            continue
        if pattern.search(text):
            hits.append(kind)
    return hits


# ---------------------------------------------------------------------------
# Pattern table. Order MATTERS: the most structural / longest-match patterns
# run first (private-key blocks, connection strings, JWTs) so a later, narrower
# pattern cannot carve a sub-match out of an already-typed region. Each entry is
# (kind, compiled_pattern, group_index_to_replace). group 0 means "replace the
# whole match"; a positive group means "replace only that capture group" (used
# for connection-strings and key=value, where we keep the surrounding structure
# like the scheme/host or the key name and redact only the secret).
# ---------------------------------------------------------------------------

# A private-key PEM block, header to footer (DOTALL via [\s\S]).
_PRIVATE_KEY_RE: Pattern[str] = re.compile(
    r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z0-9 ]*PRIVATE KEY-----"
)

# JWT: header.payload.signature; always starts with the base64 of '{' = "eyJ".
# Require at least the header + payload so we do not eat a bare "eyJ..." word.
_JWT_RE: Pattern[str] = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}(?:\.[A-Za-z0-9_-]{6,})?\b"
)

# Connection string with embedded credentials: scheme://user:pass@host. Redact
# ONLY the password group so the scheme/user/host stay legible. Any URL-ish
# scheme is covered (postgres, mysql, mongodb+srv, redis, amqp, https, ...).
#
# Username is OPTIONAL ([^\s:/@]*) so a password-only string
# ``redis://:onlypass@host`` still redacts (the leak fix for password-only
# connection strings). The password group is greedy ``[^\s/]+`` so a password
# that itself contains ``@`` (``mongodb+srv://u:p@ss@host``) is matched WHOLE:
# the greedy run backtracks to the LAST ``@`` before the host (``/`` and
# whitespace bound it), so ``p@ss`` is fully redacted instead of leaking the
# ``@ss@host`` tail. A bare email has no ``scheme://`` and is never matched.
_CONNSTR_RE: Pattern[str] = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]*:)([^\s/]+)(@)"
)

# AWS Access Key ID.
_AWS_KEY_RE: Pattern[str] = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

# Slack tokens: xoxb-, xoxp-, xoxa-, xoxr-, xoxs-.
_SLACK_RE: Pattern[str] = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")

# OpenAI / OpenRouter / Anthropic style sk- keys, including sk-or-... and
# sk-proj-.... The sk-or- and sk-proj- forms are just longer sk- tokens, so one
# pattern covers them. Hyphen + underscore allowed in the token body.
_SK_RE: Pattern[str] = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")

# GitHub personal access tokens and OAuth/app tokens.
_GITHUB_RE: Pattern[str] = re.compile(
    r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}\b"
)
_GITHUB_FINEGRAINED_RE: Pattern[str] = re.compile(
    r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"
)

# Bearer tokens in an Authorization header shape. Redact only the token group so
# the "Authorization: Bearer " structure stays readable.
_BEARER_RE: Pattern[str] = re.compile(
    r"(Authorization:\s*Bearer\s+)(\S+)", re.IGNORECASE
)

# password= / password: value shapes. Conservative key set (password, passwd,
# pwd, secret, token, api_key, apikey, access_token) so a benign word is not
# eaten. The value is matched in TWO shapes:
#   - QUOTED: an opening quote, then the value lazily, to the MATCHING closing
#     quote (``\3``) OR end-of-line (``$``) if the quote is unterminated. This
#     is the leak fix for ``password="my secret passphrase value"``: a quoted
#     secret containing spaces is consumed WHOLE instead of leaking everything
#     after the first space.
#   - UNQUOTED: a run up to the next whitespace / quote / comma / semicolon.
# Groups: 1=key, 2=separator, 3=open-quote, 4=quoted-value, 5=close-quote-or-EOL,
# 6=unquoted-value. Exactly one of group 4 / group 6 is set per match.
_PASSWORD_KV_RE: Pattern[str] = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token)"
    r"(\s*[:=]\s*)"
    r"(?:"
    r"(['\"])(.*?)(\3|$)"
    r"|"
    r"([^\s'\";,]+)"
    r")"
)


def luhn_valid(digits: str) -> bool:
    """Return True if ``digits`` (a run of 0-9) passes the Luhn checksum.

    Used to gate payment-card redaction: only Luhn-valid 13-19 digit runs are
    treated as cards, so an arbitrary numeric id (a port, a timestamp) is NOT
    redacted. Pure function.
    """
    if not digits or not digits.isdigit():
        return False
    total = 0
    # Luhn: double every second digit from the right.
    for index, ch in enumerate(reversed(digits)):
        value = ord(ch) - 48
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


# Candidate card: 13-19 digit run, optionally split by single spaces or hyphens
# in groups (e.g. 4111 1111 1111 1111). We match the spaced/hyphenated form then
# Luhn-check the digit-only collapse, so only real cards are redacted.
_CARD_CANDIDATE_RE: Pattern[str] = re.compile(
    r"\b(?:\d[ -]?){12,18}\d\b"
)


# Whole-match (group 0) replacements, in priority order.
_WHOLE_MATCH_PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("private_key", _PRIVATE_KEY_RE),
    ("jwt", _JWT_RE),
    ("aws_key", _AWS_KEY_RE),
    ("slack_token", _SLACK_RE),
    ("github_token", _GITHUB_FINEGRAINED_RE),
    ("github_token", _GITHUB_RE),
    ("api_key", _SK_RE),
]

REDACTION_KINDS = frozenset(
    {
        "private_key",
        "jwt",
        "aws_key",
        "slack_token",
        "github_token",
        "api_key",
        "connection_string",
        "bearer_token",
        "password",
        "card_number",
    }
)


def _redact_cards(text: str, hits: List[str]) -> str:
    """Replace Luhn-valid card numbers with ``[REDACTED:card_number]``.

    Only a candidate run whose digit-only collapse is Luhn-valid is redacted, so
    a bare numeric identifier (port, count, timestamp) survives untouched.
    """

    def _sub(match: "re.Match[str]") -> str:
        raw = match.group(0)
        digits = re.sub(r"[ -]", "", raw)
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            hits.append("card_number")
            return _ph("card_number")
        return raw

    return _CARD_CANDIDATE_RE.sub(_sub, text)


def redact(text: str) -> Tuple[str, List[str]]:
    """Redact secrets / credentials / high-sensitivity PII to typed placeholders.

    Returns ``(redacted_text, hits)`` where ``hits`` is the list of redaction
    KIND strings applied (e.g. ``["aws_key", "private_key"]``), one entry per
    replacement, in application order. ``hits`` is empty when nothing matched
    (the common case), so the caller can cheaply tell whether a fact carried a
    secret.

    Idempotent in spirit: re-running ``redact`` on already-redacted text finds
    nothing new (the placeholders contain no secret-shaped substrings), so the
    second pass returns ``(same_text, [])``.
    """
    if not text:
        return text, []

    hits: List[str] = []
    out = text

    # 1) Structural / longest-match whole-match replacements first, so a later
    #    narrower pattern cannot re-match inside an already-typed region.
    for kind, pattern in _WHOLE_MATCH_PATTERNS:
        def _sub(match: "re.Match[str]", _kind: str = kind) -> str:
            hits.append(_kind)
            return _ph(_kind)

        out = pattern.sub(_sub, out)

    # 2) Connection strings: redact ONLY the password group, keep scheme/user/host.
    def _connstr_sub(match: "re.Match[str]") -> str:
        hits.append("connection_string")
        return f"{match.group(1)}{_ph('connection_string')}{match.group(3)}"

    out = _CONNSTR_RE.sub(_connstr_sub, out)

    # 3) Bearer tokens: redact only the token, keep the header structure.
    def _bearer_sub(match: "re.Match[str]") -> str:
        hits.append("bearer_token")
        return f"{match.group(1)}{_ph('bearer_token')}"

    out = _BEARER_RE.sub(_bearer_sub, out)

    # 4) password= / password: value shapes: redact only the value, keep key.
    def _pw_sub(match: "re.Match[str]") -> str:
        # Exactly one of the quoted-value group (4) or unquoted-value group (6)
        # is set per match; the quoted branch is taken when an opening quote
        # (group 3) was present.
        value = match.group(4) if match.group(3) is not None else match.group(6)
        # Skip if the value is already a placeholder (idempotence) or empty.
        if not value or value.startswith("[REDACTED:"):
            return match.group(0)
        hits.append("password")
        # Drop the surrounding quotes (the whole quoted span is the secret) so we
        # do not leave dangling quotes around the typed placeholder.
        return f"{match.group(1)}{match.group(2)}{_ph('password')}"

    out = _PASSWORD_KV_RE.sub(_pw_sub, out)

    # 5) Payment cards (Luhn-gated), last so digit-only secrets above are gone.
    out = _redact_cards(out, hits)

    return out, hits
