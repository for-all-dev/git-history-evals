#!/usr/bin/env bash
# Sequential driver for #131 budget curve: 15, 30, 100 turns x {claude-sonnet-5, openai:gpt-5.6-sol}
# x {easy (leaves), hard (whole)}. Reuses the paired sample already sliced by
# pipeline/experiments/131/setup_budget_trees.sh.
set -uo pipefail

# Run from, and write into, the MAIN repository -- never a worktree. The first run of this
# experiment kept its per-problem rows in a /dispatch worktree's gitignored scratch-wave3/;
# cleaning up the worktree destroyed them permanently (an ignored file never becomes a git
# object, so there was nothing to recover from a branch, the object store or the bucket).
# Only the aggregated TSV survived. --git-common-dir resolves the main checkout even when
# this script is invoked from inside a worktree.
REPO="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
cd "$REPO"
set -a; source .env; set +a

LOG="$REPO/scratch-wave3/run_budget_curve.log"
: > "$LOG"

declare -A MODELS=( [claude-sonnet-5]="claude-sonnet-5" [openai-gpt-5.6-sol]="openai:gpt-5.6-sol" )

for N in 15 30 100; do
  for DIRNAME in claude-sonnet-5 openai-gpt-5.6-sol; do
    MODEL="${MODELS[$DIRNAME]}"
    TREE="$REPO/scratch-wave3/budget-$N-$DIRNAME"
    echo "=== [$(date -u +%FT%TZ)] START N=$N model=$MODEL easy ===" | tee -a "$LOG"
    bash pipeline/eval_sample.sh "$TREE/easy" "$MODEL" "$N" 6 --mode leaves >> "$LOG" 2>&1
    echo "=== [$(date -u +%FT%TZ)] START N=$N model=$MODEL hard ===" | tee -a "$LOG"
    bash pipeline/eval_sample.sh "$TREE/hard" "$MODEL" "$N" 6 --mode whole >> "$LOG" 2>&1
    echo "=== [$(date -u +%FT%TZ)] DONE N=$N model=$MODEL ===" | tee -a "$LOG"
  done
done
echo "=== ALL DONE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
