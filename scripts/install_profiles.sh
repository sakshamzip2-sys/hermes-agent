#!/bin/sh
# Safe, reversible profile installer.
#
# Copies each profile template under <repo>/profile_templates/<name>/ into a
# target profiles directory. This is a plain directory copy: it does NOT run
# `hermes profile create` and never mutates any live shared config. It is
# idempotent and non-destructive: an already-installed profile is left exactly
# as it is (so a user's local edits survive a re-run), making the operation
# fully reversible by simply deleting the freshly created profile dirs.
#
# Usage:
#   scripts/install_profiles.sh [target_dir]
#
# Target directory resolution:
#   1. $1 (the first argument), if given
#   2. else $HERMES_PROFILES_DIR
#   3. else $HOME/.hermes/profiles
#
# For each template dir <name>, the whole tree is copied to <target>/<name>/
# (SOUL.md, config.yaml, and any skills/ or CONNECTORS.md). If
# <target>/<name>/SOUL.md already exists, that profile is skipped.
#
# Exits 0 on success.

set -eu

# Resolve repo root from this script's location (scripts/ -> repo root).
script_dir=$(cd "$(dirname "$0")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
templates_dir="$repo_root/profile_templates"

# Resolve target dir: argv first, then env, then default under HOME.
if [ "$#" -ge 1 ] && [ -n "${1:-}" ]; then
  target="$1"
else
  target="${HERMES_PROFILES_DIR:-$HOME/.hermes/profiles}"
fi

if [ ! -d "$templates_dir" ]; then
  echo "error: no profile_templates dir at $templates_dir" >&2
  exit 1
fi

mkdir -p "$target"

installed=0
skipped=0

# Iterate every immediate child dir of profile_templates. Using a glob keeps
# this POSIX/bash-3.2 friendly (no `find -print0`, no mapfile).
for template in "$templates_dir"/*/; do
  # If the glob matched nothing literally, skip the unexpanded pattern.
  [ -d "$template" ] || continue

  # Strip the trailing slash, then take the basename as the profile name.
  template="${template%/}"
  name=$(basename "$template")
  dest="$target/$name"

  if [ -f "$dest/SOUL.md" ]; then
    echo "skip $name (exists)"
    skipped=$((skipped + 1))
    continue
  fi

  # Fresh install: recursive copy of the whole template tree. `cp -R src/.`
  # copies the contents into an existing dest dir without nesting.
  mkdir -p "$dest"
  cp -R "$template/." "$dest/"
  echo "installed $name"
  installed=$((installed + 1))
done

echo "summary: installed $installed, skipped $skipped"
exit 0
