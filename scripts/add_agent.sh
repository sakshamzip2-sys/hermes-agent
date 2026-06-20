#!/usr/bin/env bash
# Scaffold a new specialized-agent manifest (guardrail 7: reproducible add path).
#
# Usage: scripts/add_agent.sh <slug>
# Writes to $HERMES_AGENTS_DIR if set, else <repo>/.hermes/agents/<slug>.md.
# Refuses to overwrite an existing manifest. The created file is a complete,
# parseable manifest with clearly marked spots for you to fill in.

set -eu

slug="${1:-}"
if [ -z "$slug" ]; then
  echo "usage: scripts/add_agent.sh <slug>" >&2
  exit 2
fi
case "$slug" in
  [a-z0-9]*) : ;;
  *) echo "slug must start with [a-z0-9] and match [a-z0-9][a-z0-9-]*" >&2; exit 2 ;;
esac
case "$slug" in
  *[!a-z0-9-]*) echo "slug must match [a-z0-9][a-z0-9-]* (no traversal, no separators)" >&2; exit 2 ;;
esac

if [ -n "${HERMES_AGENTS_DIR:-}" ]; then
  dir="$HERMES_AGENTS_DIR"
else
  dir="$(cd "$(dirname "$0")/.." && pwd)/.hermes/agents"
fi
mkdir -p "$dir"
target="$dir/$slug.md"
if [ -e "$target" ]; then
  echo "refusing to overwrite existing $target" >&2
  exit 1
fi

cat > "$target" <<EOF
---
name: $slug
display_name: $slug
tagline: One line describing this agent (edit me)
status: active
schema_version: 1
toolsets: [file, web, memory]
permission_mode: default
memory: user
effort: medium
starters:
  - name: Example starter
    message: "Describe a task for $slug"
memory_seed: |
  # $slug — Memory
  ## How I work
  - (edit me)
---
You are $slug, a specialized agent from OpenComputer. Describe the role, how it
works, and its standards here (edit me). Keep capability real: grant only the
toolsets it needs above.
EOF

echo "created $target"
