#!/usr/bin/env python3
"""#133 selection. Two phases:

`pre`  — from depth*/mined.jsonl keep records whose (repo, file, corollary) is in the
         paired EASY sample and which have exactly N deletions at depth N; keep only
         tuples present at ALL depths; write depth*/pre/<repo>.jsonl for validation.
         Also reports how many depth-1 challenge_ids byte-match the paired sample
         (those arms' 50-turn outcomes are reusable).

`post` — after par_dryrun/keep_good, intersect the tuples that validated at ALL
         depths, cap at 100 with a seeded shuffle, and write the final eval trees
         depth*/eval/{<repo>.jsonl, manifest.json} in the layout eval_sample.sh expects.
"""

import json
import pathlib
import random
import sys
from collections import defaultdict

ROOT = pathlib.Path("/home/q/Documents/Work/safeguarded/forall/git-history-evals")
SW = ROOT / "scratch-wave3" / "depth-sweep"
DEPTHS = [1, 2, 3, 5]
CAP = 100


def paired_index():
    """(short_repo, file_path, corollary_name) -> paired challenge_id, repo -> src,
    and the URL-form repo (as the ablator emits it) -> short manifest name map."""
    idx, srcs, url_to_short = {}, {}, {}
    for entry in json.load(open(ROOT / "scratch-wave3/paired/easy/manifest.json")):
        repo = entry["repo"]
        srcs[repo] = entry["src"]
        for line in open(ROOT / f"scratch-wave3/paired/easy/{repo}.jsonl"):
            r = json.loads(line)
            url_to_short[r["repo"]] = repo
            for c in r.get("corollaries") or []:
                idx[(repo, r["file_path"], c["name"])] = r["challenge_id"]
    return idx, srcs, url_to_short


def load_mined(n: int, url_to_short: dict[str, str]):
    """(short_repo, file, corollary) -> record, keeping only exact-N-deletion records.

    The raw ablator emits `repo` in URL form (github.com/org/name); the paired manifest
    keys by short name — joining on the raw field silently matched nothing on the first
    run, hence the explicit map."""
    out = {}
    path = SW / f"depth{n}" / "mined.jsonl"
    for line in open(path):
        r = json.loads(line)
        if len(r.get("deleted_lemmas") or []) != n:
            continue
        cors = r.get("corollaries") or []
        if len(cors) != 1:
            continue
        short = url_to_short.get(r.get("repo", ""))
        if short is None:
            continue
        out[(short, r["file_path"], cors[0]["name"])] = r
    return out


def phase_pre():
    idx, srcs, url_to_short = paired_index()
    per_depth = {n: load_mined(n, url_to_short) for n in DEPTHS}
    # Distribution held fixed ACROSS DEPTHS (the #133 requirement): any corollary from
    # the paired-sample FILES whose closure supports exactly N deletions at every depth.
    # Restricting further to paired-sample corollaries would leave only 24 tuples —
    # deep closures are rare — so paired membership is recorded, not required.
    common = set(per_depth[1])
    for n in DEPTHS[1:]:
        common &= set(per_depth[n].keys())
    in_paired = {k for k in common if k in idx}
    d1_match = sum(1 for k in in_paired if per_depth[1][k]["challenge_id"] == idx[k])
    print(
        f"exact-N at all depths: {len(common)} tuples across "
        f"{len({k[0] for k in common})} repos; in paired sample: {len(in_paired)} "
        f"(depth-1 challenge_id byte-matches: {d1_match})"
    )
    rng = random.Random(42)
    pool = sorted(common)
    rng.shuffle(pool)
    keep = sorted(pool[: CAP + 30])  # buffer over the final cap for validation losses
    print(f"pre-validation selection: {len(keep)} tuples")
    for n in DEPTHS:
        by_repo = defaultdict(list)
        for k in keep:
            by_repo[k[0]].append(per_depth[n][k])
        pre = SW / f"depth{n}" / "pre"
        pre.mkdir(parents=True, exist_ok=True)
        for repo, rows in by_repo.items():
            with open(pre / f"{repo}.jsonl", "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
    (SW / "srcs.json").write_text(json.dumps(srcs, indent=1))


def phase_post():
    srcs = json.loads((SW / "srcs.json").read_text())
    good = {}
    for n in DEPTHS:
        for p in (SW / f"depth{n}").glob("good_*.jsonl"):
            short = p.stem[len("good_"):]  # filename carries the manifest short name
            for line in open(p):
                r = json.loads(line)
                key = (short, r["file_path"], (r.get("corollaries") or [{}])[0].get("name"))
                good.setdefault(n, {})[key] = r
    common = None
    for n in DEPTHS:
        keys = set(good.get(n, {}))
        common = keys if common is None else common & keys
    common = sorted(common or set())
    print(f"tuples validated at all depths: {len(common)}")
    rng = random.Random(42)
    rng.shuffle(common)
    chosen = sorted(common[:CAP])
    print(f"selected (cap {CAP}): {len(chosen)} across {len({k[0] for k in chosen})} repos")
    for n in DEPTHS:
        tree = SW / f"depth{n}" / "eval"
        tree.mkdir(parents=True, exist_ok=True)
        by_repo = defaultdict(list)
        for k in chosen:
            by_repo[k[0]].append(good[n][k])
        manifest = []
        for repo, rows in sorted(by_repo.items()):
            with open(tree / f"{repo}.jsonl", "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            manifest.append(
                {
                    "repo": repo,
                    "n": len(rows),
                    "src": srcs[repo],
                    "seed": 42,
                    "depth": n,
                    "sample_mode": "leaves",
                    "challenge_ids": [r["challenge_id"] for r in rows],
                }
            )
        (tree / "manifest.json").write_text(json.dumps(manifest, indent=1))
        print(f"depth{n}/eval: {sum(m['n'] for m in manifest)} challenges, {len(manifest)} repos")


if __name__ == "__main__":
    {"pre": phase_pre, "post": phase_post}[sys.argv[1]]()
