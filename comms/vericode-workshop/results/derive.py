#!/usr/bin/env python3
"""Derive the paper's grid statistics from the #129/#130 run trees.

Two stages, so that everything the paper cites is reproducible from *committed*
data even though the raw run trees (~95 MB of full file contents per attempt) are
not committed:

    extract   read the per-problem `res_*.jsonl` files from the run trees and
              write the compact `outcomes.tsv` (one row per solve attempt).
    report    read `outcomes.tsv` and write `derived.md` — the McNemar tests,
              the tamper-reason split, the transport-error sensitivity analysis,
              and the cost roll-up quoted in Section 5 of the paper.

Usage:
    python3 derive.py extract <run-tree>...     # e.g. scratch-wave3/paired ...
    python3 derive.py report

`aggregate.{md,json}` in this directory are verbatim copies of the aggregator's
own output for the three run trees; the macro/micro rates and their bootstrap
CIs come from there, not from this script.
"""

from __future__ import annotations

import collections
import glob
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUTCOMES = os.path.join(HERE, "outcomes.tsv")
REPORT = os.path.join(HERE, "derived.md")

# Excluded from the PASS denominator by the aggregator (see the paper, Table 2).
EXCLUDED = {"malformed", "trivial", "context_exceeded", "dry_run"}

MODE_LABEL = {"easy": "leaf", "hard": "whole"}


def outcome(d: dict) -> str:
    """Reproduce `apply_ablate.aggregate`'s outcome precedence."""
    if d.get("malformed_challenge"):
        return "malformed"
    if d.get("trivial"):
        return "trivial"
    if d.get("context_exceeded"):
        return "context_exceeded"
    if d.get("dry_run"):
        return "dry_run"
    if d.get("succeeded"):
        return "pass"
    if d.get("tampered"):
        return "tampered"
    if d.get("gave_up"):
        return "gave_up"
    if d.get("turn_limit"):
        return "turn_limit"
    if d.get("error"):
        return "error"
    return "fail"


def tamper_class(d: dict) -> str:
    """`solve.py` stores the tamper reason in `error` when `tampered` is set."""
    if not d.get("tampered"):
        return ""
    reason = str(d.get("error") or "")
    if "missing from the solution" in reason:
        return "declaration_removed"
    if "deleted or its statement weakened" in reason:
        return "statement_weakened"
    return "other"


def tree_model(tree: str) -> str:
    """The model id lives in the run tree's `agg_manifest.json`, not in the rows
    (whose `assistant` field is the *proof assistant*, always `lean` here)."""
    manifest = json.load(open(os.path.join(tree, "agg_manifest.json"), encoding="utf-8"))
    models = {e["model"] for e in manifest}
    if len(models) != 1:
        raise SystemExit(f"{tree}: expected one model per run tree, got {sorted(models)}")
    return models.pop()


def extract(trees: list[str]) -> None:
    rows = []
    for tree in trees:
        model = tree_model(tree)
        for mode in ("easy", "hard"):
            for path in sorted(glob.glob(os.path.join(tree, mode, "res_*.jsonl"))):
                repo = os.path.basename(path)[len("res_") : -len(".jsonl")]
                for line in open(path, encoding="utf-8"):
                    d = json.loads(line)
                    rows.append(
                        {
                            "model": model,
                            "mode": MODE_LABEL[mode],
                            "repo": repo,
                            "challenge_id": d["challenge_id"],
                            "outcome": outcome(d),
                            "tamper_class": tamper_class(d),
                            "max_turns": d.get("max_turns") or 0,
                            "turns_used": d.get("turns_used") or 0,
                            "input_tokens": d.get("input_tokens") or 0,
                            "output_tokens": d.get("output_tokens") or 0,
                            "elapsed_seconds": round(d.get("elapsed_seconds") or 0.0, 1),
                        }
                    )
    cols = list(rows[0])
    rows.sort(key=lambda r: (r["model"], r["mode"], r["repo"], r["challenge_id"]))
    with open(OUTCOMES, "w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[c]) for c in cols) + "\n")
    print(f"wrote {OUTCOMES}: {len(rows)} attempts")


def load_outcomes() -> list[dict]:
    with open(OUTCOMES, encoding="utf-8") as fh:
        cols = fh.readline().rstrip("\n").split("\t")
        return [dict(zip(cols, line.rstrip("\n").split("\t"))) for line in fh]


def binom_two_sided(b: int, c: int) -> float:
    """Exact McNemar (binomial) two-sided p-value on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2**n)


def macro(rows: list[dict], drop_error: bool) -> tuple[float, int, int, int]:
    excl = EXCLUDED | ({"error"} if drop_error else set())
    per: dict[str, list[int]] = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        if r["outcome"] in excl:
            continue
        per[r["repo"]][1] += 1
        if r["outcome"] == "pass":
            per[r["repo"]][0] += 1
    rates = [p / n for p, n in per.values() if n]
    npass = sum(p for p, _ in per.values())
    ntot = sum(n for _, n in per.values())
    return (sum(rates) / len(rates) if rates else 0.0), len(rates), npass, ntot


def report() -> None:
    rows = load_outcomes()
    models = sorted({r["model"] for r in rows})
    out: list[str] = []
    w = out.append
    w("# Derived statistics for the VeriCodeGen results section")
    w("")
    w("Regenerate with `python3 derive.py report` (reads `outcomes.tsv`).")
    w("Macro/micro PASS and their bootstrap CIs are NOT recomputed here — they are")
    w("quoted from the aggregator output in `grid-*.md` / `grid-*.json`.")
    w("")

    w("## Outcome composition (counts over 113 attempts per model x strategy)")
    w("")
    keys = [
        "pass",
        "tampered",
        "fail",
        "turn_limit",
        "gave_up",
        "error",
        "context_exceeded",
        "malformed",
    ]
    w("| model | strategy | " + " | ".join(keys) + " |")
    w("|---|---|" + "--:|" * len(keys))
    for m in models:
        for mode in ("leaf", "whole"):
            sub = [r for r in rows if r["model"] == m and r["mode"] == mode]
            c = collections.Counter(r["outcome"] for r in sub)
            w(f"| {m} | {mode} | " + " | ".join(str(c.get(k, 0)) for k in keys) + " |")
    w("")

    w("## Tamper reason split")
    w("")
    w("| model | strategy | declaration removed | statement weakened | total |")
    w("|---|---|--:|--:|--:|")
    for m in models:
        for mode in ("leaf", "whole"):
            sub = [r for r in rows if r["model"] == m and r["mode"] == mode]
            c = collections.Counter(r["tamper_class"] for r in sub if r["tamper_class"])
            w(
                f"| {m} | {mode} | {c.get('declaration_removed', 0)} | "
                f"{c.get('statement_weakened', 0)} | {sum(c.values())} |"
            )
    w("")

    w("## Compile-only oracle (what a scorer without the tamper guard would report)")
    w("")
    w("| model | strategy | pass | + tampered | scorable | compile-only rate | true rate |")
    w("|---|---|--:|--:|--:|--:|--:|")
    for m in models:
        for mode in ("leaf", "whole"):
            sub = [r for r in rows if r["model"] == m and r["mode"] == mode]
            scorable = [r for r in sub if r["outcome"] not in EXCLUDED]
            p = sum(1 for r in scorable if r["outcome"] == "pass")
            t = sum(1 for r in scorable if r["outcome"] == "tampered")
            n = len(scorable)
            w(
                f"| {m} | {mode} | {p} | {t} | {n} | {(p + t) / n * 100:.1f}% | "
                f"{p / n * 100:.1f}% |"
            )
    w("")

    w("## Paired comparison, leaf vs whole-body holing (exact McNemar)")
    w("")
    w("`all` counts every scorable attempt, matching the aggregator's denominator;")
    w("`no-error` additionally drops pairs where either side ended in a provider")
    w("transport error, which only matters for leanstral-1-5.")
    w("")
    w("| model | set | n pairs | leaf-only PASS | whole-only PASS | p |")
    w("|---|---|--:|--:|--:|--:|")
    for m in models:
        leaf = {r["challenge_id"]: r for r in rows if r["model"] == m and r["mode"] == "leaf"}
        whole = {r["challenge_id"]: r for r in rows if r["model"] == m and r["mode"] == "whole"}
        for label, excl in (("all", EXCLUDED), ("no-error", EXCLUDED | {"error"})):
            b = c = n = 0
            for cid, lr in leaf.items():
                wr = whole.get(cid)
                if wr is None or lr["outcome"] in excl or wr["outcome"] in excl:
                    continue
                n += 1
                lp, wp = lr["outcome"] == "pass", wr["outcome"] == "pass"
                if lp and not wp:
                    b += 1
                elif wp and not lp:
                    c += 1
            w(f"| {m} | {label} | {n} | {b} | {c} | {binom_two_sided(b, c):.3f} |")
    w("")

    w("## Transport-error sensitivity (macro PASS, repo-averaged)")
    w("")
    w("| model | strategy | macro (errors as non-pass) | repos | macro (errors dropped) | repos |")
    w("|---|---|--:|--:|--:|--:|")
    for m in models:
        for mode in ("leaf", "whole"):
            sub = [r for r in rows if r["model"] == m and r["mode"] == mode]
            a, na, _, _ = macro(sub, drop_error=False)
            b, nb, _, _ = macro(sub, drop_error=True)
            w(f"| {m} | {mode} | {a * 100:.1f}% | {na} | {b * 100:.1f}% | {nb} |")
    w("")

    w("## Cost")
    w("")
    w("Average turns is over attempts that reached a solver outcome (i.e. excluding")
    w("`malformed` and provider-error rows, which never consumed the budget).")
    w("")
    def avg_turns(sub: list[dict]) -> float:
        ran = [r for r in sub if r["outcome"] not in (EXCLUDED | {"error"})]
        return sum(int(r["turns_used"]) for r in ran) / len(ran) if ran else 0.0

    w("| model | avg turns | avg turns (leaf) | avg turns (whole) | input Mtok | output Mtok | solver time (h) |")
    w("|---|--:|--:|--:|--:|--:|--:|")
    tot_in = tot_out = tot_s = 0
    for m in models:
        sub = [r for r in rows if r["model"] == m]
        ti = sum(int(r["input_tokens"]) for r in sub)
        to = sum(int(r["output_tokens"]) for r in sub)
        ts = sum(float(r["elapsed_seconds"]) for r in sub)
        tot_in, tot_out, tot_s = tot_in + ti, tot_out + to, tot_s + ts
        w(
            f"| {m} | {avg_turns(sub):.1f} | "
            f"{avg_turns([r for r in sub if r['mode'] == 'leaf']):.1f} | "
            f"{avg_turns([r for r in sub if r['mode'] == 'whole']):.1f} | "
            f"{ti / 1e6:.1f} | {to / 1e6:.2f} | {ts / 3600:.1f} |"
        )
    w(f"| **total** | --- | --- | --- | {tot_in / 1e6:.1f} | {tot_out / 1e6:.2f} | {tot_s / 3600:.1f} |")
    w("")

    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))
    print(f"wrote {REPORT}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in {"extract", "report"}:
        print(__doc__)
        sys.exit(2)
    if sys.argv[1] == "extract":
        extract(sys.argv[2:])
    else:
        report()
