#!/usr/bin/env bash
# Parallel dry-run: shard a challenges JSONL into N pieces, run `ablate-baseline
# --dry-run` on each concurrently, merge results. The baseline uses its own tempdir
# per process so shards don't collide.
#
# All N shards read/check against the SAME $SRC concurrently (#119). Since the fix,
# `check` never invokes `lake` at all (bare `lean` only) so this is normally safe on
# its own, but `prepare` / a manual `lake build` still legitimately write into $SRC in
# place — a per-repo flock (shared while dry-run shards run, exclusive for anything
# that builds) means such a step can never race a live batch of shards, or another
# instance of itself, against the same source tree.
#
# Usage: par_dryrun.sh <challenges.jsonl> <src> <out.jsonl> [nshards]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="$(readlink -f "$1")"; SRC="$(readlink -f "$2")"; OUT="$(readlink -f "$3")"; N="${4:-16}"
LOCKDIR="${TMPDIR:-/tmp}/ablate-repo-locks"; mkdir -p "$LOCKDIR"
LOCK="$LOCKDIR/$(printf '%s' "$SRC" | sha256sum | cut -d' ' -f1).lock"
WORK="$(dirname "$OUT")/_shards_$(basename "$OUT" .jsonl)"
rm -rf "$WORK"; mkdir -p "$WORK"
# split preserving whole lines, round-robin so slow files spread across shards
python3 - "$CH" "$WORK" "$N" <<'PY'
import sys
ch, work, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
lines=[l for l in open(ch) if l.strip()]
fhs=[open(f"{work}/shard_{i}.jsonl","w") for i in range(n)]
for i,l in enumerate(lines): fhs[i%n].write(l)
for f in fhs: f.close()
print(f"{len(lines)} records into {n} shards")
PY
cd "$ROOT/baselines"
pids=()
for i in $(seq 0 $((N-1))); do
  sh="$WORK/shard_$i.jsonl"
  [ -s "$sh" ] || continue
  # shared lock: any number of shards may hold it at once, but it blocks behind an
  # exclusive holder (a `prepare` / `lake build` step against this same $SRC).
  ( flock -s 9
    uv run ablate-baseline "$sh" "$SRC" --dry-run --out "$WORK/res_$i.jsonl"
  ) 9>"$LOCK" > "$WORK/log_$i.txt" 2>&1 &
  pids+=($!)
done
echo "launched ${#pids[@]} shard workers; waiting…"
for p in "${pids[@]}"; do wait "$p"; done
cat "$WORK"/res_*.jsonl > "$OUT" 2>/dev/null
echo "merged $(wc -l < "$OUT") results -> $OUT"
