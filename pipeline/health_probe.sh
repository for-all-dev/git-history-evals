#!/usr/bin/env bash
# Health-probe each repo the way the harness actually works: take a file the miner
# produced a record for, find ITS enclosing lake root (walk up from the file — repos like
# lampe/lean-mlir have several lake roots in subdirs), and compile the PRISTINE file.
# Failure here is unambiguous: the environment is broken, not the ablation.
# Usage: health_probe.sh <scratch> <repos-file>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; LIST="$2"; cd "$ROOT"
probe_one() {
  local name="$1" src rel f p err
  src=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$S/registry_all.tsv")
  rel=$(python3 -c "import json,sys;print(json.loads(open('$S/mined/$name.jsonl').readline())['file_path'])" 2>/dev/null)
  [ -z "$rel" ] && { echo -e "$name\tNO_RECORDS"; return; }
  f="$src/$rel"
  [ -f "$f" ] || { echo -e "$name\tFILE_MISSING\t$rel"; return; }
  p=$(dirname "$f"); while [ "$p" != "." ] && [ ! -d "$p/.lake" ]; do p=$(dirname "$p"); done
  [ "$p" = "." ] && { echo -e "$name\tNO_LAKE_ROOT\t$rel"; return; }
  err=$( (cd "$p" && timeout 1200 lake env lean "${f#"$p"/}") 2>&1 | grep -E 'error|fatal' | head -1 | cut -c1-70 )
  if [ -z "$err" ]; then echo -e "$name\tHEALTHY\t$p"; else echo -e "$name\tBROKEN\t$err"; fi
}
export -f probe_one; export S
n=0
while read -r name; do
  [ -z "$name" ] && continue
  probe_one "$name" &
  n=$((n+1)); [ $((n % 8)) -eq 0 ] && wait
done < "$LIST"
wait
echo "PROBE DONE"
