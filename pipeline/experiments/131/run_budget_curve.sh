#!/usr/bin/env bash
# Sequential driver for #131 budget curve: 15, 30, 100 turns x {claude-sonnet-5, openai:gpt-5.6-sol}
# x {easy (leaves), hard (whole)}. Reuses the paired sample already sliced by scratch-wave3/setup_budget_trees.sh.
set -uo pipefail
WT="/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1"
cd "$WT"
set -a; source .env; set +a

LOG="$WT/scratch-wave3/run_budget_curve.log"
: > "$LOG"

declare -A MODELS=( [claude-sonnet-5]="claude-sonnet-5" [openai-gpt-5.6-sol]="openai:gpt-5.6-sol" )

for N in 15 30 100; do
  for DIRNAME in claude-sonnet-5 openai-gpt-5.6-sol; do
    MODEL="${MODELS[$DIRNAME]}"
    TREE="$WT/scratch-wave3/budget-$N-$DIRNAME"
    echo "=== [$(date -u +%FT%TZ)] START N=$N model=$MODEL easy ===" | tee -a "$LOG"
    bash pipeline/eval_sample.sh "$TREE/easy" "$MODEL" "$N" 6 --mode leaves >> "$LOG" 2>&1
    echo "=== [$(date -u +%FT%TZ)] START N=$N model=$MODEL hard ===" | tee -a "$LOG"
    bash pipeline/eval_sample.sh "$TREE/hard" "$MODEL" "$N" 6 --mode whole >> "$LOG" 2>&1
    echo "=== [$(date -u +%FT%TZ)] DONE N=$N model=$MODEL ===" | tee -a "$LOG"
  done
done
echo "=== ALL DONE $(date -u +%FT%TZ) ===" | tee -a "$LOG"
