#!/usr/bin/env bash
# Full rebuild of a repo's compile environment, keyed off a file the miner actually
# produced records for (walk UP from that file to its lake root — repos may have several).
#   1. drop half-cloned packages (unresolvable HEAD)
#   2. clear a stale compiled-lakefile cache (.lake/config) — `lake env` refuses otherwise
#   3. lake exe cache get  (mathlib oleans, if mathlib is a dep)
#   4. lake build          (the repo's OWN oleans — `cache get` does not build these, and
#                           a file importing a sibling module fails without them)
# Usage: rebuild_repos.sh <scratch> <repos-file>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; LIST="$2"; cd "$ROOT"
rebuild_one() {
  local name="$1" src rel f p log m
  src=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$S/registry_all.tsv")
  rel=$(python3 -c "import json;print(json.loads(open('$S/mined/$name.jsonl').readline())['file_path'])" 2>/dev/null)
  f="$src/$rel"
  p=$(dirname "$f"); while [ "$p" != "." ] && [ ! -d "$p/.lake" ]; do p=$(dirname "$p"); done
  if [ "$p" = "." ]; then p="$src"; fi     # fall back to the src root (e.g. SizzLean)
  log="$S/rebuild/$name.log"; mkdir -p "$S/rebuild"
  {
    echo "== $name  lake-root=$p  probe=$rel"
    for m in "$p"/.lake/packages/*/; do
      [ -d "$m" ] || continue
      git -C "$m" rev-parse HEAD >/dev/null 2>&1 || { echo "drop corrupt pkg $(basename "$m")"; rm -rf "$m"; }
    done
    rm -rf "$p/.lake/config"
    ( cd "$p" && timeout 5400 lake exe cache get 2>&1 | tail -1 )
    ( cd "$p" && nice -n 5 timeout 10800 lake build 2>&1 | tail -2 )
  } > "$log" 2>&1
  if (cd "$p" && timeout 1200 lake env lean "${f#"$p"/}") >/dev/null 2>&1; then echo -e "$name\tHEALTHY"
  else echo -e "$name\tSTILL_BROKEN"; fi
}
export -f rebuild_one; export S
n=0
while read -r name; do
  [ -z "$name" ] && continue
  rebuild_one "$name" &
  n=$((n+1)); [ $((n % 4)) -eq 0 ] && wait
done < "$LIST"
wait
echo "REBUILD DONE"
