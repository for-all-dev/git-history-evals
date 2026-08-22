#!/usr/bin/env python3
"""Build per-(model,budget) agg_manifest.json files for the #131 budget curve trees,
plus reuse the existing 50-turn paired/{paired,paired-openai} manifests as the 4th point.

Each manifest entry: {path, model, mode, max_turns, repo} per res_<repo>.jsonl file,
matching the schema `ablate-aggregate` (baselines/) consumes.
"""
import json
import pathlib
import sys

WT = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1")
SCRATCH = WT / "scratch-wave3"

MODEL_DIRNAME_TO_ID = {
    "claude-sonnet-5": "claude-sonnet-5",
    "openai-gpt-5.6-sol": "openai:gpt-5.6-sol",
}
MODE_TO_ABLATE_MODE = {"easy": "leaves", "hard": "whole"}


def build_for_tree(tree: pathlib.Path, model_id: str, max_turns: int):
    manifest = []
    for mode_dir, ablate_mode in MODE_TO_ABLATE_MODE.items():
        d = tree / mode_dir
        if not d.is_dir():
            continue
        for res_file in sorted(d.glob("res_*.jsonl")):
            if res_file.stat().st_size == 0:
                continue
            repo = res_file.stem[len("res_"):]
            manifest.append({
                "path": str(res_file),
                "model": model_id,
                "mode": ablate_mode,
                "max_turns": max_turns,
                "repo": repo,
            })
    out = tree / "agg_manifest.json"
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{out}: {len(manifest)} entries")
    return out


def main():
    for n in (15, 30, 100):
        for dirname, model_id in MODEL_DIRNAME_TO_ID.items():
            tree = SCRATCH / f"budget-{n}-{dirname}"
            if tree.is_dir():
                build_for_tree(tree, model_id, n)
            else:
                print(f"MISSING TREE: {tree}", file=sys.stderr)


if __name__ == "__main__":
    main()
