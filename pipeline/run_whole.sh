#!/usr/bin/env bash
# Whole-proof ablation batch: for every registered repo, mine with
# --corollary-delete-lemmas-all (one ablation per eligible corollary; each user's
# ENTIRE proof is holed, not just the leaf steps), dry-run compile challenge +
# solution, keep the good ones, finalize into artifacts/lean-ablate-whole/<repo>/.
# Idempotent: skips a repo that already has an artifact dir.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"                      # scratch dir
REG="$S/registry_all.tsv"
OUTDIR=lean-ablate-whole
MODE=--corollary-delete-lemmas-all
export ABLATE_MODE="$MODE"
cd "$ROOT"
log="$S/run_whole.log"
while IFS=$'\t' read -r name src; do
  [ -z "$name" ] && continue
  [ -d "$ROOT/artifacts/$OUTDIR/$name" ] && { echo "skip $name (done)" >>"$log"; continue; }
  echo "=== $name $(date +%H:%M:%S) ===" >>"$log"
  bash pipeline/mine_repo_mode.sh "$src" "$src" "$S/$name.jsonl" 42 90 24 >>"$log" 2>&1
  if [ ! -s "$S/$name.jsonl" ]; then echo "!! $name mined 0 records" >>"$log"; continue; fi
  bash pipeline/par_dryrun.sh "$S/$name.jsonl" "$src" "$S/${name}_dry.jsonl" 24 >>"$log" 2>&1
  python3 pipeline/keep_good.py "$S/$name.jsonl" "$S/${name}_dry.jsonl" "$S/$name.good.jsonl" >>"$log" 2>&1
  python3 pipeline/finalize_mode.py "$name" "$S/$name.jsonl" "$S/${name}_dry.jsonl" \
      "$S/$name.good.jsonl" "$src" "$OUTDIR" "$MODE" >>"$log" 2>&1
  echo "=== $name DONE: $(wc -l <"$S/$name.good.jsonl" 2>/dev/null||echo 0) good / $(wc -l <"$S/$name.jsonl") mined $(date +%H:%M:%S) ===" >>"$log"
done < "$REG"
echo "RUN_WHOLE ALL DONE $(date +%H:%M:%S)" >>"$log"
