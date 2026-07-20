#!/usr/bin/env python3
"""Join dry-run results back to the mined records by challenge_id and emit only the
GOOD ones: well-formed challenge (compiled with holes) AND ground-truth solution
compiled hole-free. Everything else (malformed / sol_BAD / trivial) is dropped.

Usage: keep_good.py <mined.jsonl> <dryrun_results.jsonl> <out.good.jsonl>
"""
import json
import sys
from collections import Counter

mined_path, dry_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]

# classify each dry-run result by challenge_id
verdict = {}
for l in open(dry_path):
    if not l.strip():
        continue
    r = json.loads(l)
    cid = r.get("challenge_id")
    if cid is None:
        continue
    if r.get("dry_run") and r.get("solution_compiles") is True:
        verdict[cid] = "good"
    elif r.get("dry_run"):
        verdict[cid] = "sol_bad"
    elif r.get("trivial"):
        verdict[cid] = "trivial"
    elif r.get("malformed_challenge"):
        verdict[cid] = "malformed"
    else:
        verdict[cid] = "other"

kept = 0
counts = Counter()
seen = set()
with open(out_path, "w") as out:
    for l in open(mined_path):
        if not l.strip():
            continue
        r = json.loads(l)
        cid = r.get("challenge_id")
        v = verdict.get(cid, "unvalidated")
        counts[v] += 1
        if v == "good" and cid not in seen:
            out.write(l)
            seen.add(cid)
            kept += 1

print(f"kept {kept} good -> {out_path}")
for k, n in counts.most_common():
    print(f"  {k:12s} {n}")
