#!/usr/bin/env bash
set -euo pipefail
WT="/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1"
SRC="/home/q/Documents/Work/safeguarded/forall/git-history-evals/scratch-wave3/paired"
for N in 15 30 100; do
  for M in claude-sonnet-5 openai-gpt-5.6-sol; do
    for MODE in easy hard; do
      DEST="$WT/scratch-wave3/budget-$N-$M/$MODE"
      mkdir -p "$DEST"
      /usr/bin/cp "$SRC/$MODE"/*.jsonl "$SRC/$MODE/manifest.json" "$DEST/"
    done
  done
done
echo done
