---
title: "Skill Security Scan"
sidebar_label: "Skill Security Scan"
description: "Scan an agent skill (or a whole skills directory) for malicious patterns and security vulnerabilities BEFORE trusting or installing it — prompt injection, da..."
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Skill Security Scan

Scan an agent skill (or a whole skills directory) for malicious patterns and security vulnerabilities BEFORE trusting or installing it — prompt injection, data exfiltration, credential/secret access, obfuscated or dangerous code (exec/eval/subprocess), curl|bash supply-chain, rogue-agent self-modification/persistence, memory poisoning, trigger abuse, and MCP tool poisoning. Wraps NVIDIA SkillSpector (64 patterns / 16 categories, 0-100 risk score). Use when the user asks whether a skill is safe, wants a downloaded/third-party/AI-generated skill vetted, wants to audit the bundled skills/ directory, or before enabling a new skill or plugin.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/skill-security-scan` |
| Version | `1.0.0` |
| Platforms | linux, macos |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that OpenComputer loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Skill Security Scan

AI agent skills run with implicit trust — a malicious `SKILL.md` or helper script can
exfiltrate secrets, run arbitrary code, or poison the agent. This skill vets a skill
**before** you trust it, using NVIDIA **SkillSpector** (a static + optional-LLM scanner,
64 vulnerability patterns across 16 categories, with a 0-100 risk score).

## When to use

- The user asks "is this skill safe?", "scan this skill", "vet this skill", or pastes a
  skill / repo / zip and wants it checked.
- Before installing or enabling any third-party, downloaded, or AI-generated skill.
- To audit the bundled `skills/` directory (or a single skill) for regressions.

## How to run

The bundled helper resolves the scanner and prints a concise verdict. **Static analysis
is the default** (fast, no credentials, no network beyond a CVE lookup):

```bash
# Scan one skill directory or a single SKILL.md
python skills/skill-security-scan/scan.py <path-to-skill-or-SKILL.md>

# Audit every skill under a directory
python skills/skill-security-scan/scan.py skills/

# Add LLM semantic analysis using THIS agent's configured provider (model-agnostic)
python skills/skill-security-scan/scan.py <path> --llm
```

The helper finds SkillSpector in this order: `$SKILLSPECTOR_BIN`, a sibling
`SkillSpector/.venv/bin/skillspector` checkout, `skillspector` on `PATH`, then a
Docker image (`skillspector`) if present. If none are found it prints install
instructions instead of failing silently.

## Interpreting the result

SkillSpector returns a **risk score (0-100)** and findings tagged by severity
(CRITICAL / HIGH / MEDIUM / LOW) and category (e.g. P3 Exfiltration, AST1 exec(),
SC2 curl|bash, RA1 self-modification).

Recommended decision rule (the helper applies it and sets a non-zero exit code when it
trips, so it can gate CI / pre-install checks):

- **SkillSpector's own `recommendation: DO_NOT_INSTALL` → do NOT install**, even if
  every finding is only MEDIUM. The wrapper gates on this (exit 2); never override the
  scanner's verdict with a looser opinion.
- **Any CRITICAL or HIGH finding, or risk score ≥ 51 → do NOT install.** Report the
  specific finding and file:line to the user and explain the risk.
- **MEDIUM only with recommendation SAFE (score &lt; 51) → caution**; summarize findings
  and ask the user.
- **LOW only / no findings → likely safe**; still surface anything notable.

Never tell the user a skill is "safe" on a clean static scan alone if it contains
network calls or executes code — recommend the `--llm` pass for those. Always quote the
concrete findings (id, severity, file, line) rather than just the score.

**Known self-scan caveat:** scanning *this* skill flags `AST4 subprocess` (it must run
the scanner) and pattern matches in this SKILL.md (which names attack patterns like
`exec`/`curl|bash`). Static analysis cannot tell "subprocess to run a security tool"
from "subprocess to run malware" — that is exactly what the `--llm` stage disambiguates.
Do not game the scanner by removing the subprocess call; this is expected for a scanner.
