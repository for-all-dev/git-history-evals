#!/usr/bin/env bash
# Prebuild l4v session heaps into the ablate heap store, so the dry-run harness can splice a
# challenge into a throwaway child session instead of rebuilding the world per challenge.
# Isabelle resolves heaps under $HOME, so we redirect HOME (not ISABELLE_HOME) at the store.
# Usage: build_l4v_heaps.sh <heap-store> <session>...
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STORE="$1"; shift
mkdir -p "$STORE"
cd "$ROOT/data/isabelle/l4v"
exec nix develop "$ROOT/ablators/isabelle/scala#isabelle-2025" -c \
  env HOME="$STORE" L4V_ARCH="${L4V_ARCH:-ARM}" isabelle build -b -j 8 -o threads=4 -d . "$@"
