"""Proof lifecycle analysis for fiat-crypto.

For each declaration that was eventually proved (Admitted removed), this script:
  1. Identifies every commit that touched the containing file before the proof
  2. At each such commit, checks whether the declaration still carried an Admitted
  3. Counts how many commits it took from first-with-hole to proof-complete
  4. Aggregates tactic groups used across those commits

Outputs:
  artifacts/fiat-crypto-lifecycle.jsonl   — per-declaration dataset
  artifacts/fiat-crypto-lifecycle-report.txt — human-readable summary

NOTE: tracking is at declaration level (not just file level). We read the
actual file content at each commit to check hole presence, so the counts
reflect commits that touched the declaration *while it still had an Admitted*.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO = Path(__file__).parents[2] / "data" / "fiat-crypto"
ARTIFACTS = Path(__file__).parents[2] / "artifacts"
CHALLENGES_PATH = ARTIFACTS / "fiat-crypto-challenges.jsonl"
COMMITS_PATH = ARTIFACTS / "fiat-crypto-commits-coq-grouped.jsonl"
OUT_JSONL = ARTIFACTS / "fiat-crypto-lifecycle.jsonl"
OUT_TXT = ARTIFACTS / "fiat-crypto-lifecycle-report.txt"

# Coq hole markers
_HOLE_RE = re.compile(r"\b(Admitted|admit|sorry|oops)\b")
# Declaration pattern — matches Theorem/Lemma/etc. followed by the name
_DECL_RE = re.compile(
    r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example"
    r"|Definition|Fixpoint|Program|Inductive|Record|Class|Instance)\s+(\w+)",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _git(*args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=False,
    )
    return r.stdout if r.returncode == 0 else ""


def file_at_commit(commit: str, path: str, _cache: dict = {}) -> str:
    key = (commit, path)
    if key not in _cache:
        _cache[key] = _git("show", f"{commit}:{path}")
    return _cache[key]


def commits_touching_file(path: str) -> list[str]:
    """Return commit hashes (oldest first) that touched this file."""
    out = _git("log", "--follow", "--format=%H", "--", path)
    hashes = [h.strip() for h in out.splitlines() if h.strip()]
    return list(reversed(hashes))  # oldest first


def decl_has_hole(content: str, decl_name: str) -> bool:
    """True if the named declaration contains an Admitted/sorry in the file."""
    if not content or not decl_name:
        return False
    # Find all declaration starts
    for m in _DECL_RE.finditer(content):
        if m.group(1) != decl_name:
            continue
        # Scan forward from this declaration to the next one or Qed/Defined
        start = m.start()
        rest = content[start:]
        # Find the end of this proof block
        end_m = re.search(r"\b(Qed|Defined|Abort|End)\b", rest)
        block = rest[: end_m.start()] if end_m else rest
        if _HOLE_RE.search(block):
            return True
    return False


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

print("Loading challenges...")
with open(CHALLENGES_PATH) as f:
    challenges = [json.loads(l) for l in f]

print("Loading commit history...")
commits_by_hash: dict[str, dict] = {}
all_commits_sorted: list[dict] = []
with open(COMMITS_PATH) as f:
    for line in f:
        r = json.loads(line)
        commits_by_hash[r["hash"]] = r
        all_commits_sorted.append(r)
all_commits_sorted.sort(key=lambda r: r["date"])

# Index commits by which files they touch
file_to_commit_hashes: dict[str, list[str]] = defaultdict(list)
for r in all_commits_sorted:
    for fp in r.get("coq_files_changed", []):
        file_to_commit_hashes[fp].append(r["hash"])

# ---------------------------------------------------------------------------
# Build proved declarations: unique (file, decl_name, proof_complete_hash)
# ---------------------------------------------------------------------------

# A declaration may appear in multiple challenges (multiple proof-complete
# commits for the same decl — e.g. proof was refactored). Deduplicate by
# (file, decl_name) keeping the LATEST proof-complete commit.
proved: dict[tuple[str, str], dict] = {}
for c in challenges:
    for h in c["holes_filled"]:
        decl = h["enclosing_decl"]
        if not decl or decl == "Definition":  # skip bare-keyword false matches
            continue
        key = (c["file_path"], decl)
        pc = commits_by_hash.get(c["commit_hash"])
        if pc is None:
            continue
        if key not in proved or pc["date"] > proved[key]["proof_complete_date"]:
            proved[key] = {
                "file": c["file_path"],
                "declaration": decl,
                "proof_complete_hash": c["commit_hash"],
                "proof_complete_date": pc["date"],
                "hole_kind": h["kind"],
            }

print(f"Unique proved declarations to analyse: {len(proved)}")

# ---------------------------------------------------------------------------
# For each proved declaration, walk the file history
# ---------------------------------------------------------------------------

results = []
total = len(proved)

for idx, ((file_path, decl_name), info) in enumerate(proved.items()):
    if idx % 100 == 0:
        print(f"  {idx}/{total} ...")

    proof_complete_date = info["proof_complete_date"]
    proof_complete_hash = info["proof_complete_hash"]

    # All commit hashes that touched this file, oldest-first, up to and
    # including the proof-complete commit
    file_hashes = file_to_commit_hashes.get(file_path, [])
    file_hashes_before = [
        h for h in file_hashes
        if (c := commits_by_hash.get(h)) and c["date"] <= proof_complete_date
    ]

    if not file_hashes_before:
        continue

    # Walk each commit and check if declaration still had a hole
    lifecycle_commits = []
    first_hole_date = None

    for ch in file_hashes_before:
        commit_rec = commits_by_hash.get(ch)
        if commit_rec is None:
            continue
        content = file_at_commit(ch, file_path)
        if not content:
            continue
        has_hole = decl_has_hole(content, decl_name)
        if has_hole:
            if first_hole_date is None:
                first_hole_date = commit_rec["date"]
            lifecycle_commits.append({
                "hash": ch,
                "date": commit_rec["date"],
                "subject": commit_rec["message_subject"],
                "commit_class": commit_rec.get("commit_class", ""),
                "tactic_groups": commit_rec.get("tactic_group_tags", []),
                "tactic_tags": commit_rec.get("tactic_tags", []),
                "net_proof_lines": commit_rec.get("diff_net_proof_lines", 0),
            })
        elif lifecycle_commits:
            # Hole disappeared — this is before the declaration existed with a hole
            # OR after it was proved. Since we're going oldest-first and stopped
            # at proof_complete_date, this means it was proved at an intermediate
            # commit and then re-opened. Keep scanning.
            pass

    if not lifecycle_commits:
        continue

    # Aggregate tactic groups across the lifecycle
    all_groups: list[str] = []
    all_tactics: list[str] = []
    group_counter: Counter[str] = Counter()
    tactic_counter: Counter[str] = Counter()
    for lc in lifecycle_commits:
        for g in lc["tactic_groups"]:
            group_counter[g] += 1
            all_groups.append(g)
        for t in lc["tactic_tags"]:
            tactic_counter[t] += 1
            all_tactics.append(t)

    unique_groups = list(group_counter.keys())
    n_commits = len(lifecycle_commits)

    # Days from first hole to proof complete
    if first_hole_date and proof_complete_date:
        from datetime import datetime
        try:
            d0 = datetime.fromisoformat(first_hole_date[:19])
            d1 = datetime.fromisoformat(proof_complete_date[:19])
            days = max(0, (d1 - d0).days)
        except Exception:
            days = -1
    else:
        days = -1

    results.append({
        "declaration": decl_name,
        "file": file_path,
        "hole_kind": info["hole_kind"],
        "proof_complete_hash": proof_complete_hash,
        "proof_complete_date": proof_complete_date,
        "first_hole_date": first_hole_date,
        "days_to_prove": days,
        "n_commits_with_hole": n_commits,
        "n_tactic_groups": len(unique_groups),
        "tactic_groups_used": unique_groups,
        "top_tactics": [t for t, _ in tactic_counter.most_common(10)],
        "group_commit_counts": dict(group_counter),
        "commit_timeline": lifecycle_commits,
    })

# ---------------------------------------------------------------------------
# Write JSONL
# ---------------------------------------------------------------------------

print(f"\nWriting {len(results)} records to {OUT_JSONL} ...")
with open(OUT_JSONL, "w") as f:
    for r in results:
        f.write(json.dumps(r) + "\n")

# ---------------------------------------------------------------------------
# Write report
# ---------------------------------------------------------------------------

commit_counts = [r["n_commits_with_hole"] for r in results]
days_list = [r["days_to_prove"] for r in results if r["days_to_prove"] >= 0]

# Distribution buckets
def bucket(n):
    if n == 1: return "1 commit"
    if n <= 3:  return "2-3 commits"
    if n <= 5:  return "4-5 commits"
    if n <= 10: return "6-10 commits"
    if n <= 20: return "11-20 commits"
    return "21+ commits"

bucket_counts: Counter[str] = Counter(bucket(n) for n in commit_counts)
BUCKET_ORDER = ["1 commit", "2-3 commits", "4-5 commits", "6-10 commits", "11-20 commits", "21+ commits"]

# Group usage
group_usage: Counter[str] = Counter()
for r in results:
    for g in r["tactic_groups_used"]:
        group_usage[g] += 1

# Top hardest declarations
results_sorted = sorted(results, key=lambda r: -r["n_commits_with_hole"])

lines = []
lines.append("=" * 70)
lines.append("PROOF LIFECYCLE REPORT — fiat-crypto")
lines.append("=" * 70)
lines.append(f"\nDeclarations tracked (proved with ≥1 commit carrying a hole): {len(results)}")
lines.append(f"Total commits-with-hole across all declarations: {sum(commit_counts)}")
lines.append("")
lines.append("COMMIT COUNT STATISTICS")
lines.append("-" * 40)
lines.append(f"  Median commits per declaration : {median(commit_counts):.0f}")
lines.append(f"  Mean   commits per declaration : {mean(commit_counts):.1f}")
lines.append(f"  Max    commits                 : {max(commit_counts)}  ({results_sorted[0]['declaration']})")
lines.append(f"  Min    commits                 : {min(commit_counts)}")
lines.append("")
lines.append("DISTRIBUTION")
lines.append("-" * 40)
total_decls = len(results)
for b in BUCKET_ORDER:
    n = bucket_counts.get(b, 0)
    pct = 100 * n / total_decls
    bar = "#" * int(pct / 2)
    lines.append(f"  {b:<15}  {n:>4}  ({pct:5.1f}%)  {bar}")
lines.append("")
if days_list:
    lines.append("TIME-TO-PROOF STATISTICS (days from first Admitted to Qed)")
    lines.append("-" * 40)
    lines.append(f"  Median : {median(days_list):.0f} days")
    lines.append(f"  Mean   : {mean(days_list):.1f} days")
    lines.append(f"  Max    : {max(days_list)} days")
    lines.append(f"  Proved same day (0 days): {sum(1 for d in days_list if d == 0)}")
    lines.append("")
lines.append("TACTIC GROUP USAGE (how many declarations used each group)")
lines.append("-" * 40)
for grp, n in group_usage.most_common():
    pct = 100 * n / total_decls
    lines.append(f"  {grp:<28}  {n:>4}  ({pct:5.1f}%)")
lines.append("")
lines.append("TOP 30 HARDEST DECLARATIONS (most commits with hole present)")
lines.append("-" * 40)
for i, r in enumerate(results_sorted[:30], 1):
    groups = ", ".join(r["tactic_groups_used"]) or "none"
    lines.append(
        f"  {i:>2}. {r['declaration']:<35}  {r['n_commits_with_hole']:>3} commits"
        f"  {r['days_to_prove']:>4}d  [{groups}]"
    )
lines.append("")
lines.append("DECLARATIONS PROVED IN A SINGLE COMMIT")
lines.append("-" * 40)
single = [r for r in results if r["n_commits_with_hole"] == 1]
lines.append(f"  Count: {len(single)}")
for r in single[:15]:
    lines.append(f"    {r['declaration']}  ({r['file'].split('/')[-1]})")
lines.append("")
lines.append("COMMIT COUNT vs TACTIC GROUPS (mean groups per commit-bucket)")
lines.append("-" * 40)
for b in BUCKET_ORDER:
    bucket_results = [r for r in results if bucket(r["n_commits_with_hole"]) == b]
    if not bucket_results:
        continue
    avg_groups = mean(r["n_tactic_groups"] for r in bucket_results)
    lines.append(f"  {b:<15}  avg tactic groups: {avg_groups:.1f}")

report = "\n".join(lines)
print(report)

with open(OUT_TXT, "w") as f:
    f.write(report + "\n")

print(f"\nWrote report to {OUT_TXT}")