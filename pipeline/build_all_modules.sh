#!/usr/bin/env bash
# Build EVERY module of a repo's own libraries, not just its default target.
# Why: a lakefile like `lean_lib «Verification» {}` (no globs) builds only the root module
# and whatever it imports. Sibling modules are never compiled, so any challenge whose file
# imports one fails with `object file ... does not exist` — which the harness records as a
# `malformed` challenge. That is an ENVIRONMENT failure being mis-attributed to the data.
# Usage: build_all_modules.sh <scratch> <repos-file>
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$1"; LIST="$2"; cd "$ROOT"
build_one() {
  local name="$1" src log
  src=$(awk -F'\t' -v n="$name" '$1==n{print $2}' "$S/registry_all.tsv")
  log="$S/buildall/$name.log"; mkdir -p "$S/buildall"
  {
    echo "== $name ($src)"
    # every lake root that actually owns mined files, derived from the mined records
    python3 - "$S/mined/$name.jsonl" "$src" <<'PY' > "$S/buildall/$name.targets"
import json,os,sys
mined,src=sys.argv[1],sys.argv[2]
roots={}
for l in open(mined):
    fp=json.loads(l)['file_path']
    f=os.path.join(src,fp)
    p=os.path.dirname(f)
    while p and p!='.' and not os.path.isdir(os.path.join(p,'.lake')): p=os.path.dirname(p)
    if not p or p=='.': continue
    rel=os.path.relpath(f,p)
    if rel.endswith('.lean'): roots.setdefault(p,set()).add(rel[:-5].replace('/','.'))
for p,mods in roots.items():
    print(p+'\t'+' '.join(sorted(mods)))
PY
    while IFS=$'\t' read -r p mods; do
      [ -z "$p" ] && continue
      echo "-- lake root $p: $(echo $mods | wc -w) modules"
      ( cd "$p" && nice -n 5 timeout 10800 lake build $mods 2>&1 | tail -2 )
    done < "$S/buildall/$name.targets"
  } > "$log" 2>&1
  echo -e "$name\tBUILT"
}
export -f build_one; export S
n=0
while read -r name; do
  [ -z "$name" ] && continue
  build_one "$name" &
  n=$((n+1)); [ $((n % 3)) -eq 0 ] && wait
done < "$LIST"
wait
echo "BUILDALL DONE"
