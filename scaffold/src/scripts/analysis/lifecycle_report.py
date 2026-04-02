"""Generate a detailed proof lifecycle report from fiat-crypto-lifecycle.jsonl."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median, stdev

ARTIFACTS = Path(__file__).parents[2] / "artifacts"
IN_PATH   = ARTIFACTS / "fiat-crypto-lifecycle.jsonl"
OUT_PATH  = ARTIFACTS / "fiat-crypto-lifecycle-report-detailed.txt"

with open(IN_PATH) as f:
    records = [json.loads(l) for l in f]

# ── helpers ──────────────────────────────────────────────────────────────────

def pct(n, total): return 100 * n / total if total else 0

def bucket(n):
    if n == 1:   return "1"
    if n <= 3:   return "2-3"
    if n <= 5:   return "4-5"
    if n <= 10:  return "6-10"
    if n <= 20:  return "11-20"
    if n <= 50:  return "21-50"
    return "51+"

BUCKET_ORDER = ["1","2-3","4-5","6-10","11-20","21-50","51+"]

ALL_GROUPS = [
    "rewrite_reduce","hypothesis_management","application",
    "meta_tactical","case_induction","contradiction_solver","arithmetic_algebra",
]

commit_counts = [r["n_commits_with_hole"] for r in records]
days_list     = [r["days_to_prove"] for r in records if r["days_to_prove"] >= 0]
N = len(records)

# ── file-level aggregation ───────────────────────────────────────────────────
file_decls  = defaultdict(list)
for r in records:
    fname = r["file"].split("/")[-1]
    file_decls[fname].append(r)

# ── tactic co-occurrence ─────────────────────────────────────────────────────
pair_counter: Counter = Counter()
for r in records:
    gs = sorted(r["tactic_groups_used"])
    for i in range(len(gs)):
        for j in range(i+1, len(gs)):
            pair_counter[(gs[i], gs[j])] += 1

# ── first vs last tactic group in timeline ───────────────────────────────────
first_group: Counter = Counter()
last_group:  Counter = Counter()
for r in records:
    tl = r["commit_timeline"]
    if not tl: continue
    fg = tl[0]["tactic_groups"]
    lg = tl[-1]["tactic_groups"]
    for g in fg: first_group[g] += 1
    for g in lg: last_group[g]  += 1

# ── net proof lines per bucket ───────────────────────────────────────────────
bucket_net: dict[str, list[int]] = defaultdict(list)
for r in records:
    b = bucket(r["n_commits_with_hole"])
    for c in r["commit_timeline"]:
        bucket_net[b].append(c["net_proof_lines"])

# ── per-group: avg commits needed ────────────────────────────────────────────
group_commit_totals: dict[str, list[int]] = defaultdict(list)
for r in records:
    for g in r["tactic_groups_used"]:
        group_commit_totals[g].append(r["n_commits_with_hole"])

# ── class sequence analysis ──────────────────────────────────────────────────
# For declarations that took ≥4 commits, what sequence of classes appeared?
class_seq_counts: Counter = Counter()
for r in records:
    if r["n_commits_with_hole"] < 4: continue
    seq = tuple(c["commit_class"] for c in r["commit_timeline"])
    class_seq_counts[seq] += 1

# Class distribution across all lifecycle commits
all_classes: Counter = Counter()
for r in records:
    for c in r["commit_timeline"]:
        all_classes[c["commit_class"]] += 1

# ── per-tactic frequency across lifecycle commits ────────────────────────────
global_tactic_freq: Counter = Counter()
for r in records:
    for c in r["commit_timeline"]:
        for t in c["tactic_tags"]:
            global_tactic_freq[t] += 1

# ── declarations that used ALL 7 groups ──────────────────────────────────────
all_seven = [r for r in records if r["n_tactic_groups"] == 7]
easy_one  = [r for r in records if r["n_commits_with_hole"] == 1]
hard      = sorted(records, key=lambda r: -r["n_commits_with_hole"])

# ── write report ─────────────────────────────────────────────────────────────

L: list[str] = []

def h1(s): L.extend(["", "=" * 72, s, "=" * 72])
def h2(s): L.extend(["", s, "-" * 60])
def row(label, val): L.append(f"  {label:<42} {val}")

h1("PROOF LIFECYCLE REPORT — fiat-crypto  (DETAILED)")
L.append(f"  Generated from: {IN_PATH.name}")
L.append(f"  Declarations tracked: {N}")
L.append(f"  Total lifecycle commits (across all declarations): {sum(commit_counts)}")

# ── 1. commit count statistics ───────────────────────────────────────────────
h2("1. COMMIT COUNT STATISTICS")
row("Total declarations tracked", N)
row("Total lifecycle commits", sum(commit_counts))
row("Median commits per declaration", f"{median(commit_counts):.0f}")
row("Mean   commits per declaration", f"{mean(commit_counts):.1f}")
row("Std dev", f"{stdev(commit_counts):.1f}")
row("Min", min(commit_counts))
row("Max", f"{max(commit_counts)}  ({hard[0]['declaration']})")
# Percentiles
sc = sorted(commit_counts)
for p in [25, 50, 75, 90, 95, 99]:
    idx = int(p / 100 * len(sc))
    row(f"p{p} percentile", sc[min(idx, len(sc)-1)])

# ── 2. distribution ──────────────────────────────────────────────────────────
h2("2. DISTRIBUTION OF COMMITS PER DECLARATION")
L.append(f"  {'Bucket':<10}  {'Count':>5}  {'Pct':>7}  Bar")
for b in BUCKET_ORDER:
    n = sum(1 for x in commit_counts if bucket(x) == b)
    bar = "█" * int(pct(n, N) / 2)
    L.append(f"  {b:<10}  {n:>5}  ({pct(n,N):5.1f}%)  {bar}")

# ── 3. time-to-proof ─────────────────────────────────────────────────────────
h2("3. TIME-TO-PROOF STATISTICS  (days: first Admitted → Qed)")
row("Declarations with timing data", len(days_list))
row("Proved same day (0 days)", f"{sum(1 for d in days_list if d == 0)}  ({pct(sum(1 for d in days_list if d == 0), len(days_list)):.1f}%)")
row("Proved within 1 week (≤7 days)", f"{sum(1 for d in days_list if d <= 7)}  ({pct(sum(1 for d in days_list if d <= 7), len(days_list)):.1f}%)")
row("Proved within 1 month (≤30 days)", f"{sum(1 for d in days_list if d <= 30)}  ({pct(sum(1 for d in days_list if d <= 30), len(days_list)):.1f}%)")
row("Median days", f"{median(days_list):.0f}")
row("Mean days", f"{mean(days_list):.1f}")
row("Max days", f"{max(days_list)}  ({[r['declaration'] for r in records if r['days_to_prove']==max(days_list)][0]})")

# ── 4. all 30 hardest ────────────────────────────────────────────────────────
h2("4. TOP 30 HARDEST DECLARATIONS  (most commits with hole present)")
L.append(f"  {'#':<3} {'Declaration':<40} {'Commits':>7} {'Days':>6}  Groups used")
L.append(f"  {'-'*3} {'-'*40} {'-'*7} {'-'*6}  {'-'*40}")
for i, r in enumerate(hard[:30], 1):
    gs = ", ".join(r["tactic_groups_used"])
    L.append(f"  {i:<3} {r['declaration']:<40} {r['n_commits_with_hole']:>7} {r['days_to_prove']:>6}  {gs}")

# ── 5. full declaration listing by file ──────────────────────────────────────
h2("5. PER-FILE BREAKDOWN  (files with ≥3 proved declarations)")
files_sorted = sorted(
    [(fname, recs) for fname, recs in file_decls.items() if len(recs) >= 3],
    key=lambda x: (-sum(r["n_commits_with_hole"] for r in x[1]))
)
for fname, recs in files_sorted[:25]:
    total_c = sum(r["n_commits_with_hole"] for r in recs)
    avg_c   = mean(r["n_commits_with_hole"] for r in recs)
    max_c   = max(r["n_commits_with_hole"] for r in recs)
    L.append(f"\n  {fname}")
    L.append(f"    declarations: {len(recs)}  |  total commits: {total_c}  |  avg: {avg_c:.1f}  |  max: {max_c}")
    for r in sorted(recs, key=lambda x: -x["n_commits_with_hole"])[:8]:
        bar = "▪" * min(r["n_commits_with_hole"], 40)
        L.append(f"    {r['declaration']:<38} {r['n_commits_with_hole']:>3} commits  {bar}")

# ── 6. tactic group usage ────────────────────────────────────────────────────
h2("6. TACTIC GROUP USAGE ACROSS LIFECYCLE")
L.append(f"  (% of declarations that used each group at least once)")
L.append(f"  {'Group':<28}  {'Count':>5}  {'Pct':>7}  {'Avg commits/decl':>18}  Bar")
for g in ALL_GROUPS:
    n  = sum(1 for r in records if g in r["tactic_groups_used"])
    av = mean(group_commit_totals[g]) if group_commit_totals[g] else 0
    bar = "█" * int(pct(n, N) / 5)
    L.append(f"  {g:<28}  {n:>5}  ({pct(n,N):5.1f}%)  {av:>18.1f}  {bar}")

# ── 7. group in first vs last commit ─────────────────────────────────────────
h2("7. TACTIC GROUPS: FIRST COMMIT vs LAST COMMIT IN LIFECYCLE")
L.append(f"  {'Group':<28}  {'In first commit':>15}  {'In last commit':>15}")
L.append(f"  {'-'*28}  {'-'*15}  {'-'*15}")
for g in ALL_GROUPS:
    fc = first_group.get(g, 0)
    lc = last_group.get(g, 0)
    L.append(f"  {g:<28}  {fc:>6} ({pct(fc,N):5.1f}%)  {lc:>6} ({pct(lc,N):5.1f}%)")

# ── 8. co-occurrence ─────────────────────────────────────────────────────────
h2("8. TACTIC GROUP CO-OCCURRENCE  (top 15 pairs across lifecycle commits)")
L.append(f"  {'Pair':<50}  {'Count':>6}")
for (a, b), n in pair_counter.most_common(15):
    L.append(f"  {a} + {b:<28}  {n:>6}")

# ── 9. commit class sequence ─────────────────────────────────────────────────
h2("9. COMMIT CLASS DISTRIBUTION ACROSS ALL LIFECYCLE COMMITS")
total_lc = sum(all_classes.values())
for cls, n in all_classes.most_common():
    L.append(f"  {cls:<20}  {n:>5}  ({pct(n, total_lc):5.1f}%)")

# ── 10. net proof lines ──────────────────────────────────────────────────────
h2("10. NET PROOF LINES ADDED PER COMMIT  (by difficulty bucket)")
L.append(f"  {'Bucket':<10}  {'Commits':>8}  {'Total net lines':>16}  {'Mean net/commit':>16}")
for b in BUCKET_ORDER:
    vals = bucket_net[b]
    if not vals: continue
    L.append(f"  {b:<10}  {len(vals):>8}  {sum(vals):>16}  {mean(vals):>16.1f}")

# ── 11. top tactics ──────────────────────────────────────────────────────────
h2("11. TOP 40 TACTICS ACROSS ALL LIFECYCLE COMMITS  (from diff added lines)")
L.append(f"  {'Tactic':<25}  {'Count':>7}")
for t, n in global_tactic_freq.most_common(40):
    bar = "█" * int(n / max(global_tactic_freq.values()) * 30)
    L.append(f"  {t:<25}  {n:>7}  {bar}")

# ── 12. declarations requiring all 7 groups ──────────────────────────────────
h2("12. DECLARATIONS THAT REQUIRED ALL 7 TACTIC GROUPS")
L.append(f"  Count: {len(all_seven)}")
L.append(f"  {'Declaration':<40}  {'Commits':>7}  {'Days':>6}  File")
for r in sorted(all_seven, key=lambda x: -x["n_commits_with_hole"])[:30]:
    L.append(f"  {r['declaration']:<40}  {r['n_commits_with_hole']:>7}  {r['days_to_prove']:>6}  {r['file'].split('/')[-1]}")

# ── 13. easy proofs (1 commit) full listing ──────────────────────────────────
h2("13. DECLARATIONS PROVED IN A SINGLE COMMIT  (full list)")
L.append(f"  Count: {len(easy_one)}")
L.append(f"  {'Declaration':<40}  {'Tactic groups':>3}  File")
for r in sorted(easy_one, key=lambda x: x["declaration"]):
    L.append(f"  {r['declaration']:<40}  {r['n_tactic_groups']:>3} groups  {r['file'].split('/')[-1]}")

# ── 14. commits with negative net lines (proof shrinkage) ───────────────────
h2("14. PROOF SHRINKAGE EVENTS  (commits with net_proof_lines < 0)")
shrink_events = [
    (r["declaration"], c["date"][:10], c["net_proof_lines"], c["subject"], r["file"].split("/")[-1])
    for r in records
    for c in r["commit_timeline"]
    if c["net_proof_lines"] < -20
]
shrink_events.sort(key=lambda x: x[2])
L.append(f"  Commits with net < -20 lines: {len(shrink_events)}")
L.append(f"  {'Declaration':<35}  {'Date':<12}  {'Net lines':>10}  Subject")
for decl, date, net, subj, fname in shrink_events[:30]:
    L.append(f"  {decl:<35}  {date:<12}  {net:>10}  {subj[:45]}")

# ── 15. commits per difficulty bucket vs tactic group depth ─────────────────
h2("15. DIFFICULTY vs TACTIC GROUP DIVERSITY")
L.append(f"  {'Bucket':<10}  {'N decls':>7}  {'Avg groups':>11}  {'% using all 7':>14}  {'Avg days':>9}")
for b in BUCKET_ORDER:
    recs_b = [r for r in records if bucket(r["n_commits_with_hole"]) == b]
    if not recs_b: continue
    avg_g   = mean(r["n_tactic_groups"] for r in recs_b)
    all7    = pct(sum(1 for r in recs_b if r["n_tactic_groups"] == 7), len(recs_b))
    avg_d   = mean(r["days_to_prove"] for r in recs_b if r["days_to_prove"] >= 0) if any(r["days_to_prove"] >= 0 for r in recs_b) else -1
    L.append(f"  {b:<10}  {len(recs_b):>7}  {avg_g:>11.1f}  {all7:>13.1f}%  {avg_d:>9.1f}")

# ── write ────────────────────────────────────────────────────────────────────
report = "\n".join(L)
with open(OUT_PATH, "w") as f:
    f.write(report + "\n")

print(report)
print(f"\nWrote to {OUT_PATH}")