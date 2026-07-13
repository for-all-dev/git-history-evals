#!/usr/bin/env bash
# Repair Lean source trees whose dep closure is broken: drop package checkouts with an
# unresolvable HEAD (half-clones: the dry-run harness symlinks .lake back into the source,
# so lake git ops write through and can corrupt it), then refetch the olean cache.
# Gate is a REAL compile of pristine source — `lake env true` passes a tree whose mathlib
# has no .olean files, which silently turns every challenge into a bogus `malformed`.
# Usage: repair_repos.sh <scratch> <repo>...
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; shift; cd "$ROOT"; mkdir -p "$S/repair"
repair_one() {
  local name="$1" src p m log f rel
  src=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$S/registry_all.tsv")
  [ -z "$src" ] && { echo "RESULT $name NO_SRC"; return; }
  p="$src"; while [ "$p" != "." ] && [ ! -d "$p/.lake" ]; do p=$(dirname "$p"); done
  [ "$p" = "." ] && { echo "RESULT $name NO_LAKE_ROOT"; return; }
  log="$S/repair/$name.log"
  {
    echo "== $name (lake root: $p)"
    for m in "$p"/.lake/packages/*/; do
      [ -d "$m" ] || continue
      git -C "$m" rev-parse HEAD >/dev/null 2>&1 || { echo "dropping corrupt pkg $(basename "$m")"; rm -rf "$m"; }
    done
    ( cd "$p" && timeout 5400 lake exe cache get 2>&1 | tail -2 )
  } > "$log" 2>&1
  f=$(find "$src" -name '*.lean' -not -path '*/.lake/*' | grep -v lakefile | head -1)
  rel=${f#"$p"/}
  if (cd "$p" && timeout 900 lake env lean "$rel") >/dev/null 2>&1; then echo "RESULT $name HEALTHY"
  else echo "RESULT $name STILL_BROKEN ($rel)"; fi
}
n=0
for name in "$@"; do
  repair_one "$name" &
  n=$((n+1)); [ $((n % 4)) -eq 0 ] && wait
done
wait
echo "REPAIR DONE"
