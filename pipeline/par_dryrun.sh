#!/usr/bin/env bash
# Parallel dry-run: shard a challenges JSONL into N pieces, run `ablate-baseline
# --dry-run` on each concurrently, merge results. The baseline uses its own tempdir
# per process so shards don't collide.
#
# Usage: par_dryrun.sh <challenges.jsonl> <src> <out.jsonl> [nshards]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="$(readlink -f "$1")"; SRC="$(readlink -f "$2")"; OUT="$(readlink -f "$3")"; N="${4:-16}"
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
  uv run ablate-baseline "$sh" "$SRC" --dry-run --out "$WORK/res_$i.jsonl" > "$WORK/log_$i.txt" 2>&1 &
  pids+=($!)
done
echo "launched ${#pids[@]} shard workers; waiting…"
for p in "${pids[@]}"; do wait "$p"; done
cat "$WORK"/res_*.jsonl > "$OUT" 2>/dev/null
echo "merged $(wc -l < "$OUT") results -> $OUT"
