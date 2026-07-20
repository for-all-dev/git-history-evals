#!/usr/bin/env python3
"""Regenerate artifacts/lean-ablate-whole/{_index.json,README.md} from the per-repo manifests."""
from __future__ import annotations
import glob, hashlib, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


idx: dict = {"dataset": "lean-ablate", "proof_assistant": "lean", "repos": {}}
for mode, tree in [
    ("corollary-leaves", "artifacts/lean-ablate"),
    ("corollary-whole", "artifacts/lean-ablate-whole"),
]:
    for m in sorted(glob.glob(str(ROOT / tree / "*" / "manifest.json"))):
        d = os.path.dirname(m)
        repo = os.path.basename(d)
        j = json.load(open(m))
        c = j["counts"]
        e = idx["repos"].setdefault(
            repo,
            {
                "git_repo": j.get("git_repo"),
                "revision": j.get("revision"),
                "lean_toolchain": j.get("lean_toolchain"),
                "modes": {},
            },
        )
        e["modes"][mode] = {
            "validated": c.get("well_formed") or c.get("good_kept") or 0,
            "mined": c.get("mined_total", 0),
            "malformed": c.get("malformed", 0),
            "sol_bad": c.get("sol_bad", 0),
            "blobs": {
                f: {"sha256": sha(os.path.join(d, f)), "bytes": os.path.getsize(os.path.join(d, f))}
                for f in ("challenges.jsonl", "challenges.all.jsonl")
                if os.path.exists(os.path.join(d, f))
            },
        }
idx["totals"] = {
    mo: {
        "validated": sum(r["modes"][mo]["validated"] for r in idx["repos"].values() if mo in r["modes"]),
        "mined": sum(r["modes"][mo]["mined"] for r in idx["repos"].values() if mo in r["modes"]),
    }
    for mo in ("corollary-leaves", "corollary-whole")
}
idx["note"] = (
    "Records are deduplicated on the (challenge, solution) text — what a solver sees. An "
    "earlier release keyed dedup on the (deleted lemma, corollary) pair and shipped ~2x the "
    "records, half of them byte-identical challenges under different challenge_ids."
)
out = ROOT / "artifacts/lean-ablate-whole/_index.json"
out.write_text(json.dumps(idx, indent=2) + "\n")
print("index totals:", idx["totals"])
