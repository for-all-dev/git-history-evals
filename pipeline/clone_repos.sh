#!/usr/bin/env bash
# Clone (or fetch) every source repo in repos.tsv at its pinned revision, then materialise any
# out-of-band dependencies listed in repo_deps.tsv.
#
# The source repos are NOT submodules: they are large (multi-GB once built) and only their
# URL+revision matters for reproducing a mining run. Every dataset manifest records the same
# pair, so a published dataset is always traceable to an exact tree.
#
# repo_deps.tsv exists because some repos depend on a checkout that Lake CANNOT pin. SymCrust's
# lakefile says `require aeneas from "../../../aeneas/backends/lean"` — a filesystem path, with no
# revision anywhere in the build. Its README pins Aeneas at d71d2e3f in prose, and nothing enforces
# it: point that path at a different Aeneas and the build dies with `Unknown constant
# Aeneas.Std.core.cmp.PartialEq.ne.trait_default` — the generated Rust model citing internals that
# revision does not have. Worse, ANY sibling `aeneas/` resolves by accident, so a wrong one looks
# like it works and then fails deep in the build. Pin it here or it is not reproducible.
#
#   usage: clone_repos.sh [<language>]        # default: all
set -uo pipefail
cd "$(dirname "$0")/.."
LANG_FILTER="${1:-}"

tail -n +2 pipeline/repos.tsv | while IFS=$'\t' read -r name lang url rev root path toolchain; do
  [ -z "$name" ] && continue
  [ -n "$LANG_FILTER" ] && [ "$lang" != "$LANG_FILTER" ] && continue
  # `root` is where the git clone lives; `path` is the Lean project inside it (lake root / mine
  # dir). They differ when a repo needs a workspace wrapper: symcrust's clone sits at
  # data/lean/symcrust/SymCrypt so that its OWN pinned aeneas can be its sibling, because its
  # lakefile path-requires ../../../aeneas (see repo_deps.tsv).
  if [ ! -d "$root/.git" ]; then
    echo "== clone $name -> $root"
    git clone --quiet "$url" "$root" || { echo "!! clone failed: $name"; continue; }
  fi
  git -C "$root" fetch --quiet --all
  # checkout by sha, so revisions on non-default branches work (symcrust's lives on
  # feature/verifiedcrypto, which is why a repo-name search never finds it)
  git -C "$root" checkout --quiet "$rev" || echo "!! revision $rev not found in $name"
  echo "$name @ $(git -C "$root" rev-parse --short HEAD)  (toolchain: ${toolchain:-n/a})"
done

# out-of-band dependencies: path-requires that Lake cannot pin (see the header)
if [ -f pipeline/repo_deps.tsv ]; then
  tail -n +2 pipeline/repo_deps.tsv | while IFS=$'\t' read -r repo dep url rev dest; do
    [ -z "$repo" ] && continue
    if [ ! -d "$dest/.git" ]; then
      echo "== $repo needs $dep (unpinnable path-require) -> $dest"
      git clone --quiet "$url" "$dest" || { echo "!! clone failed: $dep"; continue; }
    fi
    git -C "$dest" fetch --quiet --all
    git -C "$dest" checkout --quiet "$rev" || echo "!! revision $rev not found in $dep"
    echo "  $repo/$dep @ $(git -C "$dest" rev-parse --short HEAD)"
  done
fi
