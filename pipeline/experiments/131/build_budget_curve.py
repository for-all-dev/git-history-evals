#!/usr/bin/env python3
"""Build pipeline/budget_curve.tsv from the per-(model,budget) aggregate.json files
(#131) plus the tamper-reason breakdown (#136). Run after build_agg_manifests.py +
ablate-aggregate have produced aggregate.json for each budget-<N>-<model> tree, and
after the existing 50-turn paired{,-openai} aggregate.json are available.
"""
import json
import pathlib
import subprocess
import sys

WT = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1")
SCRATCH = WT / "scratch-wave3"
MAIN_SCRATCH = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/scratch-wave3")
TAMPER_SCRIPT = SCRATCH / "tamper_breakdown.py"

# (label, tree_dir, agg_json_path) per (model, budget)
POINTS = []
for n in (15, 30, 100):
    for dirname, model in (("claude-sonnet-5", "claude-sonnet-5"), ("openai-gpt-5.6-sol", "openai:gpt-5.6-sol")):
        tree = SCRATCH / f"budget-{n}-{dirname}"
        POINTS.append((model, n, tree, tree / "aggregate.json"))
# existing 50-turn point, reused from #129/#130 (do NOT rerun)
POINTS.append(("claude-sonnet-5", 50, MAIN_SCRATCH / "paired", MAIN_SCRATCH / "paired" / "aggregate.json"))
POINTS.append(("openai:gpt-5.6-sol", 50, MAIN_SCRATCH / "paired-openai", MAIN_SCRATCH / "paired-openai" / "aggregate.json"))


def tamper_counts(tree: pathlib.Path):
    if not tree.is_dir():
        return None
    out = subprocess.run(
        [sys.executable, str(TAMPER_SCRIPT), str(tree / "easy" / "res_*.jsonl"), str(tree / "hard" / "res_*.jsonl")],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    return json.loads(out.stdout)


def main():
    rows = []
    gaps = []
    for model, n, tree, agg_path in POINTS:
        if not agg_path.is_file():
            gaps.append(f"MISSING aggregate.json for model={model} budget={n} (expected {agg_path})")
            continue
        agg = json.loads(agg_path.read_text())
        by_mode = {e["mode"]: e for e in agg}
        tamper = tamper_counts(tree)
        for mode_label, ablate_mode in (("easy", "leaves"), ("hard", "whole")):
            e = by_mode.get(ablate_mode)
            if e is None:
                gaps.append(f"MISSING mode={ablate_mode} in aggregate for model={model} budget={n}")
                continue
            o = e["outcomes"]
            rows.append({
                "model": model,
                "budget": n,
                "mode": mode_label,
                "total": e["total"],
                "scorable": e["scorable"],
                "pass": o["pass"],
                "micro_rate": e["micro_rate"],
                "macro_rate": e["macro_rate"],
                "macro_ci_lo": e["macro_ci_lo"],
                "macro_ci_hi": e["macro_ci_hi"],
                "tampered": o["tampered"],
                "turn_limit": o["turn_limit"],
                "turn_limit_frac": (o["turn_limit"] / e["scorable"]) if e["scorable"] else None,
            })
        if tamper is not None:
            rows[-1]["tamper_deleted"] = tamper["by_reason"]["deleted"]
            rows[-1]["tamper_weakened"] = tamper["by_reason"]["weakened"]

    tsv_path = WT / "pipeline" / "budget_curve.tsv"
    cols = ["model", "budget", "mode", "total", "scorable", "pass", "micro_rate", "macro_rate",
            "macro_ci_lo", "macro_ci_hi", "tampered", "turn_limit", "turn_limit_frac"]
    with open(tsv_path, "w") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")
    print(f"wrote {tsv_path} ({len(rows)} rows)")
    if gaps:
        print("GAPS:")
        for g in gaps:
            print(" -", g)
    (WT / "pipeline" / "budget_curve_gaps.txt").write_text("\n".join(gaps) + ("\n" if gaps else ""))


if __name__ == "__main__":
    main()
