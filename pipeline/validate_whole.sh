#!/usr/bin/env bash
# Compile-validate the ALREADY-MINED whole-proof batch ($S/mined/<repo>.jsonl) and
# rewrite artifacts/lean-ablate-whole/<repo>/ with the validated split:
#   challenges.jsonl      = good (challenge compiles with holes + solution compiles clean)
#   challenges.all.jsonl  = all mined records
#   manifest.json         = provenance + validation counts
# Skips repos whose lake root is still unhealthy (their challenges would all be reported
# `malformed` for environmental reasons, which would be a lie about the data).
#
# Usage: validate_whole.sh <scratch-dir>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; cd "$ROOT"
MODE=--corollary-delete-lemmas-all
OUTDIR=lean-ablate-whole
log="$S/validate_whole.log"
mkdir -p "$S/dry"
while IFS=$'\t' read -r name src; do
  [ -z "$name" ] && continue
  mined="$S/mined/$name.jsonl"
  [ -s "$mined" ] || { echo "skip $name (no mined records)" >>"$log"; continue; }
  # health gate: lake must resolve deps in the source tree, else every record reads malformed
  # Health gate: the PRISTINE source file must compile. Anything weaker is not a gate —
  # `lake env true` only checks dependency *resolution*, so it passes a tree whose mathlib
  # is checked out but has no .olean files, and then every challenge in the repo comes back
  # `malformed` for environmental reasons (this produced bogus 0/93, 0/958 runs). It also
  # false-positived under lake lock contention. A real compile of untouched source can't lie.
  # Probe with a file the miner actually produced a record for, and find ITS lake root by
  # walking UP FROM THE FILE (repos like lampe/lean-mlir/etheorem have several lake roots in
  # subdirs). Probing "the first .lean in the tree" picks test files (CvxLeanTest.lean) or
  # files from never-built sub-libraries, and then healthy repos get skipped.
  probe=$(python3 -c "import json;print(json.loads(open('$S/mined/$name.jsonl').readline())['file_path'])" 2>/dev/null)
  [ -z "$probe" ] && { echo "SKIP $name (no mined records)" >>"$log"; continue; }
  pf="$src/$probe"
  p=$(dirname "$pf"); while [ "$p" != "." ] && [ ! -d "$p/.lake" ]; do p=$(dirname "$p"); done
  [ "$p" = "." ] && p="$src"
  if ! (cd "$p" && timeout 1200 lake env lean "${pf#"$p"/}") >/dev/null 2>&1; then
    echo "SKIP $name (pristine mined file does not compile — env broken, not validating)" >>"$log"; continue
  fi
  [ -s "$S/dry/$name.jsonl" ] && { echo "skip $name (already dry-run)" >>"$log"; continue; }
  echo "=== $name $(date +%H:%M:%S) ===" >>"$log"
  bash pipeline/par_dryrun.sh "$mined" "$src" "$S/dry/$name.jsonl" 24 >>"$log" 2>&1
  python3 pipeline/keep_good.py "$mined" "$S/dry/$name.jsonl" "$S/dry/$name.good.jsonl" >>"$log" 2>&1
  python3 pipeline/finalize_mode.py "$name" "$mined" "$S/dry/$name.jsonl" \
      "$S/dry/$name.good.jsonl" "$src" "$OUTDIR" "$MODE" >>"$log" 2>&1
  echo "=== $name DONE: $(wc -l <"$S/dry/$name.good.jsonl" 2>/dev/null||echo 0) good / $(wc -l <"$mined") mined ===" >>"$log"
done < "$S/registry_all.tsv"
echo "VALIDATE_WHOLE DONE $(date +%H:%M:%S)" >>"$log"
