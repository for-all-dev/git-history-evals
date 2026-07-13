#!/usr/bin/env python3
"""Finalize a validated ablation dataset into artifacts/lean-ablate/<repo>/:
  - challenges.jsonl  : the GOOD (validated) records
  - challenges.all.jsonl : all mined records (reference)
  - manifest.json     : provenance (repo, revision, toolchain), ablator flags,
                        seed, and validation counts.

Usage: finalize.py <repo_name> <mined.jsonl> <dryrun.jsonl> <good.jsonl> <src_dir>
"""
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

repo_name, mined, dry, good, src = sys.argv[1:6]
ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "artifacts" / "lean-ablate" / repo_name
out.mkdir(parents=True, exist_ok=True)

# provenance from the first mined record (ablator already stamped repo/revision)
first = json.loads(next(l for l in open(mined) if l.strip()))
gitrepo, revision = first.get("repo"), first.get("revision")
toolchain = None
tc = Path(src) / "lean-toolchain"
if tc.exists():
    toolchain = tc.read_text().strip()

# validation tally
counts = Counter()
for l in open(dry):
    if not l.strip():
        continue
    r = json.loads(l)
    if r.get("dry_run") and r.get("solution_compiles") is True:
        counts["well_formed"] += 1
    elif r.get("dry_run"):
        counts["sol_bad"] += 1
    elif r.get("malformed_challenge"):
        counts["malformed"] += 1
    elif r.get("trivial"):
        counts["trivial"] += 1
    else:
        counts["other"] += 1

n_good = sum(1 for l in open(good) if l.strip())
n_all = sum(1 for l in open(mined) if l.strip())

shutil.copyfile(good, out / "challenges.jsonl")
shutil.copyfile(mined, out / "challenges.all.jsonl")

manifest = {
    "repo": repo_name,
    "git_repo": gitrepo,
    "revision": revision,
    "lean_toolchain": toolchain,
    "proof_assistant": "lean",
    "ablator": "ablators/lean (corollary-delete-lemmas-leaves-all)",
    "ablator_flags": [
        "--corollary-delete-lemmas-leaves-all",
        "--shrink-solution-minimal",
        "--compact",
        "--seed",
        "42",
    ],
    "seed": 42,
    "validation": "ablate-baseline --dry-run (preflight compile of challenge + solution)",
    "counts": {
        "mined_total": n_all,
        "good_kept": n_good,
        **dict(counts),
    },
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"{repo_name}: {n_good}/{n_all} good -> {out}")
print("  provenance:", gitrepo, revision, toolchain)
