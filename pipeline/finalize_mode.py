#!/usr/bin/env python3
"""Finalize a validated ablation dataset into artifacts/<outdir>/<repo>/.

Same as finalize.py but parameterised by the artifact tree + ablator mode, so the
whole-proof (`--corollary-delete-lemmas-all`) batch can live beside the leaf batch.

Usage: finalize_mode.py <repo> <mined.jsonl> <dryrun.jsonl> <good.jsonl> <src> <outdir> <mode-flag>
"""

import json
import shutil
import sys
from collections import Counter
from pathlib import Path

repo_name, mined, dry, good, src, outdir, mode = sys.argv[1:8]
ROOT = Path(__file__).resolve().parent.parent
out = ROOT / "artifacts" / outdir / repo_name
out.mkdir(parents=True, exist_ok=True)

first = json.loads(next(l for l in open(mined) if l.strip()))
gitrepo, revision = first.get("repo"), first.get("revision")
toolchain = None
tc = Path(src) / "lean-toolchain"
if tc.exists():
    toolchain = tc.read_text().strip()

counts = Counter()
for line in open(dry):
    if not line.strip():
        continue
    r = json.loads(line)
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
# On a re-validation the mined file IS the artifact's challenges.all.jsonl; copying it onto
# itself raises SameFileError and aborts before the manifest is written.
if Path(mined).resolve() != (out / "challenges.all.jsonl").resolve():
    shutil.copyfile(mined, out / "challenges.all.jsonl")

manifest = {
    "repo": repo_name,
    "git_repo": gitrepo,
    "revision": revision,
    "lean_toolchain": toolchain,
    "proof_assistant": "lean",
    "ablator": f"ablators/lean ({mode.lstrip('-')})",
    "ablator_flags": [mode, "--shrink-solution-minimal", "--compact", "--seed", "42"],
    "seed": 42,
    "validation": "ablate-baseline --dry-run (preflight compile of challenge + solution)",
    "counts": {"mined_total": n_all, "good_kept": n_good, **dict(counts)},
}
(out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(f"{repo_name}: {n_good}/{n_all} good -> {out}")
