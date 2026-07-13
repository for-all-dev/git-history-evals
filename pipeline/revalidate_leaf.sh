#!/usr/bin/env bash
# Re-validate the LEAF batch (artifacts/lean-ablate/<repo>/challenges.all.jsonl) against a
# repaired build environment, and rewrite that repo's artifact (challenges.jsonl + manifest).
# The leaf datasets were validated against trees with unbuilt modules / a broken FFI, so
# their `malformed` counts were inflated and their valid sets are understated.
# Usage: revalidate_leaf.sh <scratch> <repo>...
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; shift; cd "$ROOT"
mkdir -p "$S/leafdry"
for name in "$@"; do
  src=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$S/registry_all.tsv")
  mined="artifacts/lean-ablate/$name/challenges.all.jsonl"
  [ -s "$mined" ] || { echo "skip $name (no mined leaf records)"; continue; }
  echo "=== leaf re-validate $name ($(wc -l < "$mined") records) $(date +%H:%M:%S)"
  bash pipeline/par_dryrun.sh "$mined" "$src" "$S/leafdry/$name.jsonl" 24
  python3 pipeline/keep_good.py "$mined" "$S/leafdry/$name.jsonl" "$S/leafdry/$name.good.jsonl"
  python3 pipeline/finalize_mode.py "$name" "$mined" "$S/leafdry/$name.jsonl" \
      "$S/leafdry/$name.good.jsonl" "$src" "lean-ablate" "--corollary-delete-lemmas-leaves-all"
  echo "=== $name LEAF DONE: $(wc -l < "$S/leafdry/$name.good.jsonl") good / $(wc -l < "$mined") mined"
done
echo "LEAF REVALIDATE DONE"
