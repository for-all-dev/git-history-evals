#!/usr/bin/env python3
"""#131 acceptance item: sanity-check the post-hoc thresholding assumption against the
genuine low-budget re-runs.

Issue #131 proposed deriving the whole budget curve from one high-budget run by
thresholding on turns_used (a problem solved at turn 12 would have been solved at any
budget >= 12). But the agent is TOLD its budget (solve.py `_budget`, and the system
prompt), so behaviour can depend on the announced budget and thresholding may not be
equivalent. We ran genuine arms at 15/30/100, so we can measure the discrepancy
directly: for B in {15, 30}, compare

  thresholded(B) = passes of the 100-turn arm with turns_used <= B
  genuine(B)     = passes of the real B-turn arm

per (model, mode), on the shared challenge_id set. Reports pass counts, the
directional discordance (thresholded-only vs genuine-only ids), and McNemar-style
exact binomial p on the discordant pairs.
"""

import glob
import json
import pathlib
from math import comb

WT = pathlib.Path(
    "/home/q/Documents/Work/safeguarded/forall/git-history-evals/.claude/worktrees/wf_fb82a8e0-b56-1"
)
SCRATCH = WT / "scratch-wave3"


def load(tree: pathlib.Path, mode: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for f in glob.glob(str(tree / mode / "res_*.jsonl")):
        for line in open(f):
            r = json.loads(line)
            rows[r["challenge_id"]] = r
    return rows


def passed(r: dict) -> bool:
    return bool(r.get("succeeded")) and not r.get("tampered")


def binom_two_sided(k: int, n: int) -> float:
    if n == 0:
        return 1.0
    p = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2**n * 2
    return min(1.0, p)


def main() -> None:
    out_lines = []
    for dirname, model in (
        ("claude-sonnet-5", "claude-sonnet-5"),
        ("openai-gpt-5.6-sol", "openai:gpt-5.6-sol"),
    ):
        hundred = SCRATCH / f"budget-100-{dirname}"
        for mode in ("easy", "hard"):
            r100 = load(hundred, mode)
            for b in (15, 30):
                genuine = load(SCRATCH / f"budget-{b}-{dirname}", mode)
                shared = set(r100) & set(genuine)
                thr = {
                    cid
                    for cid in shared
                    if passed(r100[cid]) and (r100[cid].get("turns_used") or 10**9) <= b
                }
                gen = {cid for cid in shared if passed(genuine[cid])}
                only_thr = sorted(thr - gen)
                only_gen = sorted(gen - thr)
                n_disc = len(only_thr) + len(only_gen)
                p = binom_two_sided(len(only_thr), n_disc)
                out_lines.append(
                    {
                        "model": model,
                        "mode": mode,
                        "budget": b,
                        "n_shared": len(shared),
                        "thresholded_pass": len(thr),
                        "genuine_pass": len(gen),
                        "thresholded_only": only_thr,
                        "genuine_only": only_gen,
                        "mcnemar_p": round(p, 4),
                    }
                )
                print(
                    f"{model:22s} {mode:4s} B={b:3d}  thr={len(thr):3d}  gen={len(gen):3d}  "
                    f"disc(thr-only/gen-only)={len(only_thr)}/{len(only_gen)}  p={p:.3f}"
                )
    out = SCRATCH / "threshold_equivalence.json"
    out.write_text(json.dumps(out_lines, indent=1) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
