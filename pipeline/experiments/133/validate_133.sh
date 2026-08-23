#!/usr/bin/env bash
# #133 validation phases only (mining is already done): select pre -> par_dryrun/keep_good
# per (depth, repo) -> select post (final eval trees). Detached-safe, resumable: a repo
# whose good_ file already exists is skipped.
set -uo pipefail
ROOT=/home/q/Documents/Work/safeguarded/forall/git-history-evals
SW="$ROOT/scratch-wave3/depth-sweep"
cd "$ROOT"
DEPTHS="1 2 3 5"

rm -rf "$SW"/depth*/pre "$SW"/depth*/'dry_*.jsonl' "$SW"/depth*/'good_*.jsonl'
python3 "$SW/select_133.py" pre

for N in $DEPTHS; do
  echo "== validate depth$N $(date -Is)"
  for f in "$SW/depth$N"/pre/*.jsonl; do
    [ -e "$f" ] || continue
    repo="$(basename "$f" .jsonl)"
    [ -s "$SW/depth$N/good_$repo.jsonl" ] && continue
    src="$(awk -F'\t' -v r="$repo" '$1==r{print $2; exit}' "$SW/files.tsv")"
    bash pipeline/par_dryrun.sh "$f" "$src" "$SW/depth$N/dry_$repo.jsonl" 8
    python3 pipeline/keep_good.py "$f" "$SW/depth$N/dry_$repo.jsonl" "$SW/depth$N/good_$repo.jsonl"
  done
done
echo "== final common-set trees $(date -Is)"
python3 "$SW/select_133.py" post
echo "VALIDATE133 DONE $(date -Is)"
