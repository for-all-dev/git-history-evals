#!/usr/bin/env python3
"""Score a difficulty model's predictions against real harness outcomes.

Discrimination (ROC-AUC: can it *rank* hard vs easy?) and calibration (Brier score, plus its
Murphy decomposition into reliability / resolution / uncertainty — does 0.7 actually mean 70%?).
Those come apart: a model can rank well yet be systematically over-confident, which is exactly
what `class_weight="balanced"` training produces.

Non-scorable outcomes (malformed / trivial / context_exceeded) are dropped: the model never
saw those problems, so they are not evidence about the *solver*.

Usage: score_predictions.py <scored.jsonl> <results.jsonl>
"""

from __future__ import annotations

import json
import re
import sys

OVER = re.compile(r"too large for model|maximum context length", re.I)


def main() -> None:
    scored_path, results_path = sys.argv[1], sys.argv[2]
    pred = {}
    for line in open(scored_path):
        r = json.loads(line)
        # the model emits `difficulty` = P(fail); we want P(pass)
        pred[r["challenge_id"]] = 1.0 - float(r["difficulty"])

    y, p, dropped = [], [], 0
    for line in open(results_path):
        r = json.loads(line)
        cid = r.get("challenge_id")
        if cid not in pred:
            continue
        err = r.get("error") or ""
        if (
            r.get("context_exceeded")
            or OVER.search(err)
            or r.get("malformed_challenge")
            or r.get("trivial")
        ):
            dropped += 1
            continue
        y.append(1 if r.get("succeeded") else 0)
        p.append(pred[cid])

    n = len(y)
    if not n:
        print("no scorable rows")
        return
    base = sum(y) / n

    # ROC-AUC via the Mann-Whitney U identity (mean rank of positives), ties averaged
    order = sorted(range(n), key=lambda i: p[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and p[order[j + 1]] == p[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    npos = sum(y)
    nneg = n - npos
    auc = (
        (sum(ranks[i] for i in range(n) if y[i]) - npos * (npos + 1) / 2) / (npos * nneg)
        if npos and nneg
        else float("nan")
    )

    brier = sum((p[i] - y[i]) ** 2 for i in range(n)) / n
    brier_base = sum((base - y[i]) ** 2 for i in range(n)) / n  # always predict base rate

    # Murphy decomposition over deciles: Brier = reliability - resolution + uncertainty
    bins: dict[int, list[int]] = {}
    for i in range(n):
        bins.setdefault(min(int(p[i] * 10), 9), []).append(i)
    reliability = resolution = 0.0
    for idxs in bins.values():
        nk = len(idxs)
        pbar = sum(p[i] for i in idxs) / nk
        obar = sum(y[i] for i in idxs) / nk
        reliability += nk * (pbar - obar) ** 2
        resolution += nk * (obar - base) ** 2
    reliability /= n
    resolution /= n
    uncertainty = base * (1 - base)

    print(f"held-out: {n} scorable ({dropped} non-scorable dropped)")
    print(f"  base rate (actual PASS)   : {base:.3f}")
    print(f"  mean predicted P(pass)    : {sum(p) / n:.3f}")
    print("")
    print(f"  DISCRIMINATION  ROC-AUC   : {auc:.3f}   (0.5 = coin flip, 1.0 = perfect ranking)")
    print(f"  CALIBRATION     Brier     : {brier:.3f}   (lower is better)")
    print(f"                  baseline  : {brier_base:.3f}   (always predict the base rate)")
    print(f"                  skill     : {1 - brier / brier_base:+.3f}   (>0 beats the base rate)")
    print("")
    print(f"  Murphy decomposition: reliability {reliability:.3f} (miscalibration, lower better)")
    print(f"                        resolution  {resolution:.3f} (informativeness, higher better)")
    print(f"                        uncertainty {uncertainty:.3f} (irreducible)")
    print("")
    print("  reliability diagram (predicted -> actual):")
    for b in sorted(bins):
        idxs = bins[b]
        pbar = sum(p[i] for i in idxs) / len(idxs)
        obar = sum(y[i] for i in idxs) / len(idxs)
        print(
            f"    [{b / 10:.1f},{(b + 1) / 10:.1f})  n={len(idxs):3d}  "
            f"predicted {pbar:.2f}  actual {obar:.2f}"
        )


if __name__ == "__main__":
    main()
