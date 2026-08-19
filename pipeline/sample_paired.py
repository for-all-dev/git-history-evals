#!/usr/bin/env python3
"""Draw a PAIRED easy/hard sample: the same problem set, scored under both ablation modes.

The paper's headline claim is that `hard` (whole-proof holing) is meaningfully harder than
`easy` (leaf holing). That comparison is only honest if both splits are scored on the SAME
problems -- otherwise a difference in pass rate could just be a difference in which problems
were drawn.

Pairing is a join, not a search, because `challenge_id` does not encode mode:

    challenge_id = sha1_16(file_path | seed | variant | sorted(deleted_lemma_names)
                            | sorted(holed_theorem_names))

(ablators/lean/Ablator/Record.lean:87-96). Both modes enumerate the same corollary openers
and delete the same lemmas at a fixed seed; they differ only in how the deleted lemma's
in-file *users* get holed. So a challenge mined in `leaves` mode and its counterpart mined in
`whole` mode carry the SAME challenge_id, and pairing is just intersecting the two id sets.

Usage: sample_paired.py <out-dir> <n> [--exclude <other-sample.jsonl> ...] [--seed <seed>]

Emits `<out-dir>/easy/` (leaves) and `<out-dir>/hard/` (whole), each shaped exactly like
`sample_disjoint.py`'s output (per-repo `<repo>.jsonl`, `manifest.json`, `sample.jsonl`) so
`eval_sample.sh <out-dir>/easy <model> ... --mode leaves` and
`eval_sample.sh <out-dir>/hard <model> ... --mode whole` work unmodified. Every row in both
trees is tagged `sample_mode` ("leaves" / "whole") -- the same field `eval_sample.sh` already
stamps onto result rows -- so a downstream consumer that pools both modes' results into one
file can still join correctly by keying on `(challenge_id, sample_mode)` instead of a bare
`challenge_id` (see `pipeline/score_predictions.py` and
`baselines/src/apply_ablate/difficulty/dataset.py`).

Allocation policy and TEXT-level dedup are reused from `sample_disjoint.py` verbatim: 2
problems/repo (shortfall filled 1-at-a-time from the repos with the FEWEST remaining pairs
first), and dedup is on the challenge TEXT (sha256 of `challenge_file_content`), not
`challenge_id` -- an earlier ablator shipped byte-identical challenges under different ids,
which would otherwise leak problems across a split. Dedup is applied independently within
each mode's pool BEFORE intersecting, so a duplicate-text row surviving in one mode but not
the other cannot smuggle a repeated problem back in through the pairing.
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

# Overridable for isolated testing (subprocess tests can point this at a scratch tree without
# touching the real repo's artifacts/ or pipeline/registry_all.tsv).
ROOT = Path(os.environ.get("PIPELINE_SAMPLE_ROOT") or Path(__file__).resolve().parent.parent)

# out-dir subdir name -> (sample_mode tag, artifact tree). Values match sample_disjoint.py's
# `--mode` vocabulary exactly, so eval_sample.sh's `--mode leaves|whole` and the `sample_mode`
# it stamps onto results line up with what we stamp onto challenges here.
DIR_MODE = {
    "easy": "leaves",
    "hard": "whole",
}
MODE_ARTIFACT_DIR = {
    "leaves": "artifacts/lean-ablate",
    "whole": "artifacts/lean-ablate-whole",
}


def h(rec: dict) -> str:
    return hashlib.sha256(rec["challenge_file_content"].encode()).hexdigest()


def load_pool(mode: str, root: Path | None = None) -> dict[str, dict[str, str]]:
    """Per-repo {challenge_id: jsonl-line} for one mode's artifact tree.

    TEXT-deduped (first-seen wins, matching `sample_disjoint.py`) and tagged with
    `sample_mode`. Rows without a `challenge_id` are skipped: pairing needs a stable join key,
    and legacy pre-enriched-schema rows predate it.

    `root` defaults to the *current* module-level `ROOT` (looked up at call time, not at
    def time), so tests can `monkeypatch.setattr(sample_paired, "ROOT", tmp_path)` and have
    it take effect for callers -- like `main()` -- that don't pass `root` explicitly.
    """
    root = ROOT if root is None else root
    pool: dict[str, dict[str, str]] = {}
    glob_pattern = str(root / MODE_ARTIFACT_DIR[mode] / "*/challenges.jsonl")
    for f in sorted(glob.glob(glob_pattern)):
        repo = os.path.basename(os.path.dirname(f))
        seen_text: set[str] = set()
        rows: dict[str, str] = {}
        for line in open(f):
            if not line.strip():
                continue
            rec = json.loads(line)
            cid = rec.get("challenge_id")
            if not cid:
                continue
            tkey = h(rec)
            if tkey in seen_text:
                continue
            seen_text.add(tkey)
            rec["sample_mode"] = mode
            rows[cid] = json.dumps(rec) + "\n"
        if rows:
            pool[repo] = rows
    return pool


def intersect(
    easy_pool: dict[str, dict[str, str]], hard_pool: dict[str, dict[str, str]]
) -> dict[str, list[str]]:
    """repo -> sorted challenge_ids present (post text-dedup) in BOTH pools."""
    out: dict[str, list[str]] = {}
    for repo in sorted(set(easy_pool) & set(hard_pool)):
        ids = sorted(set(easy_pool[repo]) & set(hard_pool[repo]))
        if ids:
            out[repo] = ids
    return out


def allocate(counts: dict[str, int], n_target: int) -> dict[str, int]:
    """sample_disjoint.py's policy: 2/repo, shortfall from the fewest-remaining repos first."""
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
    return take


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    out_dir = argv[0]
    n_target = int(argv[1])
    excluded: set[str] = set()
    seed = 42

    if "--exclude" in argv:
        for p in argv[argv.index("--exclude") + 1 :]:
            if p.startswith("--"):
                break
            excluded |= {h(json.loads(line)) for line in open(p) if line.strip()}

    if "--seed" in argv:
        seed = int(argv[argv.index("--seed") + 1])

    reg = dict(
        line.rstrip("\n").split("\t")
        for line in open(ROOT / "pipeline/registry_all.tsv")
        if line.strip()
    )

    easy_pool = load_pool("leaves")
    hard_pool = load_pool("whole")
    pairs = intersect(easy_pool, hard_pool)

    if excluded:
        pruned: dict[str, list[str]] = {}
        for repo, ids in pairs.items():
            kept = [
                cid
                for cid in ids
                if h(json.loads(easy_pool[repo][cid])) not in excluded
                and h(json.loads(hard_pool[repo][cid])) not in excluded
            ]
            if kept:
                pruned[repo] = kept
        pairs = pruned

    counts = {r: len(ids) for r, ids in pairs.items()}
    take = allocate(counts, n_target)

    out = Path(out_dir)
    shutil.rmtree(out, ignore_errors=True)
    easy_dir, hard_dir = out / "easy", out / "hard"
    easy_dir.mkdir(parents=True)
    hard_dir.mkdir(parents=True)

    manifests = {"easy": [], "hard": []}
    n = 0
    for repo, k in sorted(take.items()):
        if not k:
            continue
        ids = pairs[repo][:]
        random.Random(seed).shuffle(ids)
        picked = ids[:k]
        for tag, pool, d in (("easy", easy_pool, easy_dir), ("hard", hard_pool, hard_dir)):
            rows = [pool[repo][cid] for cid in picked]
            (d / f"{repo}.jsonl").write_text("".join(rows))
            manifests[tag].append(
                {
                    "repo": repo,
                    "n": len(picked),
                    "src": reg[repo],
                    "seed": seed,
                    "sample_mode": DIR_MODE[tag],
                    "challenge_ids": picked,
                }
            )
        n += len(picked)

    for tag, d in (("easy", easy_dir), ("hard", hard_dir)):
        json.dump(manifests[tag], open(d / "manifest.json", "w"), indent=1)
        with open(d / "sample.jsonl", "w") as out_f:
            for m in manifests[tag]:
                out_f.writelines(open(d / f"{m['repo']}.jsonl").readlines())

    easy_ids = {json.loads(line)["challenge_id"] for line in open(easy_dir / "sample.jsonl")}
    hard_ids = {json.loads(line)["challenge_id"] for line in open(hard_dir / "sample.jsonl")}
    assert easy_ids == hard_ids, "paired samples must share an identical challenge_id set"
    print(
        f"paired-sampled {n} problems across {len(manifests['easy'])} repos -> "
        f"{out_dir}/{{easy,hard}} ({len(easy_ids)} shared challenge_ids)"
    )


if __name__ == "__main__":
    main()
