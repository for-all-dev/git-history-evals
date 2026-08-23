#!/usr/bin/env bash
# #133 eval arms: the 100-problem common set at depths 1/2/3/5, claude-sonnet-5,
# 50 turns, leaves mode. Two depths run concurrently (2 x 6-way = 12-way API, the
# level that ran smoothly all day). Ends with per-depth aggregation and the decay
# table pipeline/deletion_curve.tsv (worktree-independent: main tree).
set -uo pipefail
ROOT=/home/q/Documents/Work/safeguarded/forall/git-history-evals
SW="$ROOT/scratch-wave3/depth-sweep"
cd "$ROOT"; set -a; source .env; set +a
MODEL=claude-sonnet-5; TURNS=50

for PAIR in "1 2" "3 5"; do
  for N in $PAIR; do
    echo "== eval depth$N start $(date -Is)"
    bash pipeline/eval_sample.sh "$SW/depth$N/eval" "$MODEL" "$TURNS" 6 --mode leaves \
      > "$SW/depth$N/eval.log" 2>&1 &
  done
  wait
  echo "== pair [$PAIR] done $(date -Is)"
done

python3 - <<'PY'
import json, pathlib
SW = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/scratch-wave3/depth-sweep")
for n in (1, 2, 3, 5):
    tree = SW / f"depth{n}" / "eval"
    manifest = []
    for res in sorted(tree.glob("res_*.jsonl")):
        if res.stat().st_size == 0:
            continue
        manifest.append({
            "path": str(res), "model": "claude-sonnet-5", "mode": "leaves",
            "max_turns": 50, "repo": res.stem[len("res_"):],
        })
    (tree / "agg_manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"depth{n}: agg manifest {len(manifest)} repos")
PY
for N in 1 2 3 5; do
  ( cd baselines && uv run ablate-aggregate "$SW/depth$N/eval/agg_manifest.json" \
      --out-json "$SW/depth$N/eval/aggregate.json" \
      --out-md "$SW/depth$N/eval/aggregate.md" --seed 42 ) || echo "aggregate failed depth$N"
done

python3 - <<'PY'
import json, pathlib
SW = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/scratch-wave3/depth-sweep")
out = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/pipeline/deletion_curve.tsv")
cols = ["depth", "total", "scorable", "pass", "micro_rate", "macro_rate",
        "macro_ci_lo", "macro_ci_hi", "tampered", "turn_limit", "error", "malformed"]
rows = []
for n in (1, 2, 3, 5):
    agg = json.loads((SW / f"depth{n}" / "eval" / "aggregate.json").read_text())
    e = next(x for x in agg if x["mode"] == "leaves")
    o = e["outcomes"]
    rows.append([n, e["total"], e["scorable"], o["pass"], e["micro_rate"], e["macro_rate"],
                 e["macro_ci_lo"], e["macro_ci_hi"], o["tampered"], o["turn_limit"],
                 o["error"], o["malformed"]])
with open(out, "w") as f:
    f.write("\t".join(cols) + "\n")
    for r in rows:
        f.write("\t".join(str(x) for x in r) + "\n")
print(f"wrote {out}")
for r in rows:
    print("depth", r[0], "pass", r[3], "macro", round(r[5], 3))
PY
echo "EVAL133 DONE $(date -Is)"
