#!/usr/bin/env bash
# Patch-run for the #131 sonnet 100-turn arm: the Anthropic account ran out of credits
# mid-run, killing 84 rows at turn 0 ("credit balance is too low", 76 easy-side 400s + 8
# surfaced as ModelAPIError). Re-solve ONLY those rows, splice them into the
# budget-100-claude-sonnet-5 res files, re-aggregate, and rebuild pipeline/budget_curve.tsv.
set -uo pipefail
WT=/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1
TREE="$WT/scratch-wave3/budget-100-claude-sonnet-5"
PATCH="$WT/scratch-wave3/sonnet100-patch"
cd "$WT"; set -a; source .env; set +a

python3 - <<'PY'
import json, glob, pathlib
WT = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1")
TREE = WT / "scratch-wave3/budget-100-claude-sonnet-5"
PATCH = WT / "scratch-wave3/sonnet100-patch"
for m in ("easy", "hard"):
    dead = set()
    for f in glob.glob(str(TREE / m / "res_*.jsonl")):
        for l in open(f):
            r = json.loads(l)
            if r.get("error") and "credit balance" in str(r["error"]):
                dead.add(r["challenge_id"])
    out = PATCH / m
    out.mkdir(parents=True, exist_ok=True)
    manifest, kept = [], 0
    for entry in json.load(open(TREE / m / "manifest.json")):
        repo = entry["repo"]
        rows = [json.loads(l) for l in open(TREE / m / f"{repo}.jsonl")]
        want = [r for r in rows if r["challenge_id"] in dead]
        if not want:
            continue
        with open(out / f"{repo}.jsonl", "w") as f:
            for r in want:
                f.write(json.dumps(r) + "\n")
        e = dict(entry)
        e["n"] = len(want)
        e["challenge_ids"] = [r["challenge_id"] for r in want]
        manifest.append(e)
        kept += len(want)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(m, "credit-dead rows:", kept, "across", len(manifest), "repos")
PY

for m in easy hard; do
  MODE=leaves; [ "$m" = hard ] && MODE=whole
  echo "== sonnet100 patch $m start $(date -Is)"
  bash pipeline/eval_sample.sh "$PATCH/$m" claude-sonnet-5 100 6 --mode "$MODE"
done

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
echo "SONNET100 PATCH DONE $(date -Is)"
