#!/usr/bin/env bash
# Resume the sonnet-100 credit-error patch after the 17:5x task kill: easy is complete
# (46/46 rows), hard stopped at 10/38. Re-runs ONLY hard repos whose res file is missing
# or short (per-repo re-run granularity — completed repos are not re-paid), then runs the
# original splice + aggregate + budget-curve tail from run_sonnet100_patch.sh.
set -uo pipefail
WT=/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1
PATCH="$WT/scratch-wave3/sonnet100-patch"
FIN="$PATCH/hard-finish"
cd "$WT"; set -a; source .env; set +a

python3 - <<'PY'
import json, pathlib, shutil
PATCH = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1/scratch-wave3/sonnet100-patch")
FIN = PATCH / "hard-finish"
if FIN.exists():
    shutil.rmtree(FIN)
FIN.mkdir()
manifest = json.load(open(PATCH / "hard" / "manifest.json"))
todo = []
for e in manifest:
    repo = e["repo"]
    slice_rows = sum(1 for _ in open(PATCH / "hard" / f"{repo}.jsonl"))
    res = PATCH / "hard" / f"res_{repo}.jsonl"
    done_rows = sum(1 for _ in open(res)) if res.exists() else 0
    if done_rows < slice_rows:
        shutil.copy(PATCH / "hard" / f"{repo}.jsonl", FIN / f"{repo}.jsonl")
        todo.append(e)
(FIN / "manifest.json").write_text(json.dumps(todo, indent=1))
print("hard repos to (re)run:", len(todo), "of", len(manifest))
PY

echo "== sonnet100 hard-finish start $(date -Is)"
bash pipeline/eval_sample.sh "$FIN" claude-sonnet-5 100 6 --mode whole
# copy finished res files back over the partial/missing ones
for f in "$FIN"/res_*.jsonl; do
  [ -s "$f" ] && cp "$f" "$PATCH/hard/$(basename "$f")"
done

# ---- original tail: splice fresh rows into budget-100 tree, re-aggregate, rebuild curve
python3 - <<'PY'
import json, glob, pathlib
WT = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1")
TREE = WT / "scratch-wave3/budget-100-claude-sonnet-5"
PATCH = WT / "scratch-wave3/sonnet100-patch"
for m, lbl in (("easy", "leaves"), ("hard", "whole")):
    fresh = {}
    for f in glob.glob(str(PATCH / m / "res_*.jsonl")):
        for l in open(f):
            r = json.loads(l); r["sample_mode"] = lbl
            fresh[r["challenge_id"]] = r
    spliced = 0
    for f in glob.glob(str(TREE / m / "res_*.jsonl")):
        rows = [json.loads(l) for l in open(f)]
        out = []
        for r in rows:
            if r["challenge_id"] in fresh:
                out.append(fresh.pop(r["challenge_id"])); spliced += 1
            else:
                out.append(r)
        open(f, "w").write("".join(json.dumps(r) + "\n" for r in out))
    print(m, "spliced", spliced, "unspliced-fresh", len(fresh))
    files = sorted(glob.glob(str(TREE / m / "res_*.jsonl")))
    with open(TREE / m / "results.jsonl", "w") as out:
        for f in files:
            out.write(open(f).read())
PY

( cd baselines && uv run ablate-aggregate ../scratch-wave3/budget-100-claude-sonnet-5/agg_manifest.json \
    --out-json ../scratch-wave3/budget-100-claude-sonnet-5/aggregate.json \
    --out-md ../scratch-wave3/budget-100-claude-sonnet-5/aggregate.md --seed 42 ) || echo "aggregate failed"
python3 "$WT/scratch-wave3/build_budget_curve.py"
echo "SONNET100 PATCH FINISH DONE $(date -Is)"
