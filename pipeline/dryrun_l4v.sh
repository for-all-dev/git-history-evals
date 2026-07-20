#!/usr/bin/env bash
# Dry-run (pre-flight compile) l4v challenges under the OFFICIAL Isabelle2025 from the scala
# flake — nixpkgs' isabelle has broken SMT reconstruction, and l4v @ 429d778 needs the base
# 2025 (not the 2025-2 point release). ABLATE_ISABELLE_HOME points at the prebuilt heap store
# (isabelle resolves heaps under $HOME, so the harness redirects HOME for the subprocess).
# Usage: dryrun_l4v.sh <challenges.jsonl> <out.jsonl> [shards]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="$1"; OUT="$2"; N="${3:-8}"
export L4V_ARCH="${L4V_ARCH:-ARM}"
export ABLATE_ISABELLE_HOME="$ROOT/.ablate-heaps"
exec nix develop "$ROOT/ablators/isabelle#isabelle-2025" -c \
  bash "$ROOT/pipeline/par_dryrun.sh" "$CH" "$ROOT/data/isabelle/l4v" "$OUT" "$N"
