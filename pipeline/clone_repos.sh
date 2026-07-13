#!/usr/bin/env bash
# Clone (or fetch) every source repo in repos.tsv at its pinned revision.
# The source repos are NOT submodules: they are large (multi-GB once built) and only their
# URL+revision matters for reproducing a mining run. Every dataset manifest records the same
# pair, so a published dataset is always traceable to an exact tree.
#   usage: clone_repos.sh [<language>]        # default: all
set -uo pipefail
cd "$(dirname "$0")/.."
LANG_FILTER="${1:-}"
tail -n +2 pipeline/repos.tsv | while IFS=$'\t' read -r name lang url rev path toolchain; do
  [ -z "$name" ] && continue
  [ -n "$LANG_FILTER" ] && [ "$lang" != "$LANG_FILTER" ] && continue
  root="data/$lang/$name"
  if [ ! -d "$root/.git" ]; then
    echo "== clone $name -> $root"
    git clone --quiet "$url" "$root" || { echo "!! clone failed: $name"; continue; }
  fi
  git -C "$root" fetch --quiet --all
  git -C "$root" checkout --quiet "$rev" || echo "!! revision $rev not found in $name"
  echo "$name @ $(git -C "$root" rev-parse --short HEAD)  (toolchain: ${toolchain:-n/a})"
done
