#!/usr/bin/env bash
# Slice the paired sample into one tree per (budget, model, mode) for the turn-budget curve.
#
# Trees are written into the MAIN repository's scratch-wave3/, never into a worktree.
# The first run of this experiment wrote them to a /dispatch worktree's own
# scratch-wave3/; that directory is gitignored, so when the worktree was cleaned up the
# per-problem rows were unrecoverable -- not on a branch, not in the object store (an
# ignored file never becomes a git object), not in the bucket. Only the aggregated
# budget_curve.tsv survived, which is why that figure is stuck on an older denominator
# while every grid arm could be re-aggregated. Keep experiment output in the main tree.
set -euo pipefail

# --git-common-dir points at the MAIN repo's .git even when run from inside a worktree,
# so this resolves to the main checkout in both cases.
REPO="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
SRC="$REPO/scratch-wave3/paired"
[ -d "$SRC" ] || { echo "missing $SRC -- run the paired grid first" >&2; exit 1; }

for N in 15 30 100; do
  for M in claude-sonnet-5 openai-gpt-5.6-sol; do
    for MODE in easy hard; do
      DEST="$REPO/scratch-wave3/budget-$N-$M/$MODE"
      mkdir -p "$DEST"
      cp "$SRC/$MODE"/*.jsonl "$SRC/$MODE/manifest.json" "$DEST/"
    done
  done
done
echo "budget trees under $REPO/scratch-wave3/budget-*"
