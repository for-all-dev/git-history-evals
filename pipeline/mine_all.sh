#!/usr/bin/env bash
set -uo pipefail
S="$1"; cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ABLATE_MODE=--corollary-delete-lemmas-all
mkdir -p "$S/mined"
while IFS=$'\t' read -r name src; do
  [ -z "$name" ] && continue
  [ -s "$S/mined/$name.jsonl" ] && continue
  bash pipeline/mine_repo_mode.sh "$src" "$src" "$S/mined/$name.jsonl" 42 90 24 \
    2>&1 | sed "s/^/[$name] /" >> "$S/mine_all.log"
done < "$S/registry_all.tsv"
echo "MINE_ALL DONE" >> "$S/mine_all.log"
