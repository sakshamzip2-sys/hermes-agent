#!/usr/bin/env bash
# Deploy the self-contained `coding` router profile to a hermes VM.
#
# What it does (all reversible/additive):
#   1. installs the claude-code + codex CLIs on the VM (free; auth is separate)
#   2. copies this profile into the VM's ~/.hermes/profiles/coding/
#   3. verifies the SOUL loads and the three bundled skills are present
#
# It does NOT auth the CLIs or copy any credentials — that is the operator's step
# (claude / codex logins use your own account and must be done interactively).
#
# Usage:  bash deploy-coding-profile-to-vm.sh [user@host]
#   default host: root@100.124.164.84  (the agent VM on the tailnet)
set -euo pipefail

VM="${1:-root@100.124.164.84}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE_SRC="$HERE/profile_templates/coding"

[ -f "$PROFILE_SRC/SOUL.md" ] || { echo "error: profile not found at $PROFILE_SRC" >&2; exit 1; }

echo "==> Target VM: $VM"
echo "==> 1/4 installing claude-code + codex CLIs on the VM"
ssh -o ConnectTimeout=15 "$VM" 'npm install -g @anthropic-ai/claude-code @openai/codex >/dev/null 2>&1; \
  echo "    claude: $(claude --version 2>/dev/null || echo MISSING)"; \
  echo "    codex:  $(codex --version 2>/dev/null || echo MISSING)"; \
  echo "    tmux:   $(tmux -V 2>/dev/null || echo MISSING)"'

echo "==> 2/4 deploying the self-contained profile to ~/.hermes/profiles/coding/"
ssh -o ConnectTimeout=15 "$VM" 'mkdir -p ~/.hermes/profiles/coding'
# rsync if available, else tar over ssh. Trailing slash copies contents incl dotfiles.
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$PROFILE_SRC/" "$VM:.hermes/profiles/coding/"
else
  tar -C "$PROFILE_SRC" -czf - . | ssh "$VM" 'tar -C ~/.hermes/profiles/coding -xzf -'
fi

echo "==> 3/4 verifying the profile on the VM"
ssh -o ConnectTimeout=15 "$VM" 'P=~/.hermes/profiles/coding; \
  echo "    files:"; ls -1 "$P" "$P/skills"; \
  echo "    marker (.no-bundled-skills): $([ -f "$P/.no-bundled-skills" ] && echo present || echo MISSING)"; \
  echo "    SOUL identity: $(grep -m1 "Coding Router" "$P/SOUL.md" >/dev/null && echo OK || echo MISSING)"; \
  HERMES_HOME="$P" python3 - <<PY 2>/dev/null || echo "    (loader check skipped: $?)"
import os
soul = os.path.expanduser("~/.hermes/profiles/coding/SOUL.md")
print("    SOUL loads:", "OK" if "OpenComputer Coding Router" in open(soul).read() else "MISSING")
PY'

echo "==> 4/4 NEXT (operator, one-time, uses your own accounts):"
echo "    ssh $VM"
echo "    claude   # interactive login (Claude Max OAuth) or set ANTHROPIC_API_KEY"
echo "    codex login   # ChatGPT login (a Codex-enabled plan) or set OPENAI_API_KEY"
echo "    # then run the orchestrator:"
echo "    oc -p coding     # (or: hermes -p coding)"
echo "==> done. The profile is deployed; only the CLI logins remain."
