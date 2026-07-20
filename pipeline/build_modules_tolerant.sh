#!/usr/bin/env bash
# Build each mined module INDIVIDUALLY, tolerating failures.
# `lake build M1 M2 ... Mn` aborts the entire invocation on the first unknown target
# (e.g. a `bench.*` or `docs.*` module that belongs to no lean_lib), so every module after
# it silently goes unbuilt. Per-module keeps one bad target from poisoning the batch.
# Usage: build_modules_tolerant.sh <scratch> <repos-file>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; LIST="$2"; cd "$ROOT"
build_one() {
  local name="$1" log ok=0 fail=0 p mods m
  log="$S/buildall/$name.tolerant.log"
  : > "$log"
  while IFS=$'\t' read -r p mods; do
    [ -z "$p" ] && continue
    for m in $mods; do
      if ( cd "$p" && nice -n 5 timeout 1800 lake build "$m" ) >/dev/null 2>&1; then ok=$((ok+1)); else fail=$((fail+1)); echo "FAIL $m" >> "$log"; fi
    done
  done < "$S/buildall/$name.targets"
  echo -e "$name\tok=$ok\tfail=$fail"
}
export -f build_one; export S
n=0
while read -r name; do
  [ -z "$name" ] && continue
  build_one "$name" &
  n=$((n+1)); [ $((n % 3)) -eq 0 ] && wait
done < "$LIST"
wait
echo "TOLERANT BUILD DONE"
