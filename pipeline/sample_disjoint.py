#!/usr/bin/env python3
"""Draw a per-repo sample from the validated leaf ablations, deduplicated by challenge text.

Allocation: 2 problems from every repo that has >=2; repos with fewer contribute what they have;
the shortfall is made up 1-at-a-time from the repos with the FEWEST remaining problems first.

Dedup is on the challenge TEXT, not `challenge_id`: an earlier ablator shipped byte-identical
challenges under different ids, which silently leaked problems across a train/test split.

Usage: sample_disjoint.py <out-dir> <n> [--exclude <other-sample.jsonl> ...]
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def h(rec: dict) -> str:
    return hashlib.sha256(rec["challenge_file_content"].encode()).hexdigest()


def main() -> None:
    out_dir = sys.argv[1]
    n_target = int(sys.argv[2])
    excluded: set[str] = set()
    if "--exclude" in sys.argv:
        for p in sys.argv[sys.argv.index("--exclude") + 1 :]:
            if p.startswith("--"):
                break
            excluded |= {h(json.loads(line)) for line in open(p) if line.strip()}

    reg = dict(
        line.rstrip("\n").split("\t")
        for line in open(ROOT / "pipeline/registry_all.tsv")
        if line.strip()
    )
    pool: dict[str, list[str]] = {}
    for f in sorted(glob.glob(str(ROOT / "artifacts/lean-ablate/*/challenges.jsonl"))):
        repo = os.path.basename(os.path.dirname(f))
        seen: set[str] = set()
        rows = []
        for line in open(f):
            if not line.strip():
                continue
            k = h(json.loads(line))
            if k in excluded or k in seen:
                continue
            seen.add(k)
            rows.append(line)
        if rows:
            pool[repo] = rows

    counts = {r: len(v) for r, v in pool.items()}
    take = {r: min(2, c) for r, c in counts.items()}
    total = sum(take.values())
    while total < n_target:
        cand = [r for r in counts if counts[r] - take[r] > 0]
        if not cand:
            break
        cand.sort(key=lambda r: (counts[r] - take[r], r))  # fewest remaining first
        for r in cand:
            if total >= n_target:
                break
            take[r] += 1
            total += 1

    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir)
    manifest, n = [], 0
    for repo, k in sorted(take.items()):
        if not k:
            continue
        rows = pool[repo][:]
        random.Random(hash(out_dir) & 0xFFFF).shuffle(rows)
        pick = rows[:k]
        with open(f"{out_dir}/{repo}.jsonl", "w") as f:
            f.writelines(pick)
        manifest.append({"repo": repo, "n": len(pick), "src": reg[repo]})
        n += len(pick)
    json.dump(manifest, open(f"{out_dir}/manifest.json", "w"), indent=1)
    with open(f"{out_dir}/sample.jsonl", "w") as out:
        for m in manifest:
            out.writelines(open(f"{out_dir}/{m['repo']}.jsonl").readlines())
    uniq = {h(json.loads(line)) for line in open(f"{out_dir}/sample.jsonl")}
    print(
        f"sampled {n} problems across {len(manifest)} repos -> {out_dir} "
        f"({len(uniq)} unique, {len(uniq & excluded)} overlap with excluded)"
    )


if __name__ == "__main__":
    main()
