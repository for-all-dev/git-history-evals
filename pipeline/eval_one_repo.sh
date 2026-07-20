#!/usr/bin/env bash
# Run the solver over EVERY challenge of a single repo, sharded for parallelism. baseline.py
# loops challenges sequentially, so for one big repo we split its JSONL into N shards and run
# N ablate-baseline processes against the same src tree (each uses its own tempdir).
# Usage: eval_one_repo.sh <challenges.jsonl> <src> <out-dir> <model> [max_turns] [shards]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH="$(readlink -f "$1")"; SRC="$(readlink -f "$2")"; OUT="$3"; MODEL="$4"; TURNS="${5:-50}"; N="${6:-12}"
mkdir -p "$OUT"; W="$OUT/_shards"; rm -rf "$W"; mkdir -p "$W"
python3 - "$CH" "$W" "$N" <<'PY'
import sys
ch,w,n=sys.argv[1],sys.argv[2],int(sys.argv[3])
lines=[l for l in open(ch) if l.strip()]
fhs=[open(f"{w}/shard_{i}.jsonl","w") for i in range(n)]
for i,l in enumerate(lines): fhs[i%n].write(l)   # round-robin so slow files spread across shards
for f in fhs: f.close()
print(f"{len(lines)} challenges -> {n} shards")
PY
cd "$ROOT/baselines"
pids=()
for i in $(seq 0 $((N-1))); do
  sh="$W/shard_$i.jsonl"; [ -s "$sh" ] || continue
  uv run ablate-baseline "$sh" "$SRC" --model "$MODEL" --max-turns "$TURNS" \
      --out "$W/res_$i.jsonl" > "$W/log_$i.txt" 2>&1 &
  pids+=($!)
done
echo "launched ${#pids[@]} shard workers"
for p in "${pids[@]}"; do wait "$p"; done
cat "$W"/res_*.jsonl > "$OUT/results.jsonl" 2>/dev/null
echo "EVAL_ONE_REPO DONE: $(grep -c . "$OUT/results.jsonl") results"
