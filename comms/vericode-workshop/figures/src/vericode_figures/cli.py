"""`uv run figures` — regenerate every currently-producible paper figure.

Deterministic by construction: every plotted number is read straight out of committed
(or symlinked-but-content-addressed) inputs, model/mode iteration order is a fixed
constant (never a filesystem glob or dict order), and no figure draws a random sample —
the bootstrap CIs shown are pre-computed upstream by `ablate-aggregate`. Two runs over
the same inputs must produce byte-identical PDFs; see README.md for the verification
command.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .data import MissingAggregateError
from .paths import data_dir, find_repo_root, out_dir, pipeline_dir
from .plots import (
    budget_curve,
    deletion_curve,
    grid_pass,
    outcome_composition,
    tamper_by_model,
    temporal_holdout,
)

FIGURES_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    repo_root = find_repo_root()
    d_dir = data_dir(repo_root)
    p_dir = pipeline_dir(repo_root)
    o_dir = out_dir(FIGURES_PROJECT_ROOT)

    print(f"repo root:   {repo_root}")
    print(f"data dir:    {d_dir}")
    print(f"pipeline dir:{p_dir}")
    print(f"out dir:     {o_dir}\n")

    steps = [
        ("grid-pass", lambda: grid_pass.render(d_dir, o_dir)),
        ("outcome-composition", lambda: outcome_composition.render(d_dir, o_dir)),
        ("tamper-by-model", lambda: tamper_by_model.render(d_dir, o_dir)),
        ("temporal-holdout", lambda: temporal_holdout.render(p_dir, o_dir)),
        ("budget-curve", lambda: budget_curve.render(p_dir, o_dir)),
        ("deletion-curve", lambda: deletion_curve.render(p_dir, o_dir)),
    ]

    written: list[Path] = []
    for name, fn in steps:
        print(f"[{name}]")
        try:
            paths = fn()
        except MissingAggregateError as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            return 1
        for p in paths:
            print(f"  wrote {p.relative_to(FIGURES_PROJECT_ROOT)}")
        written.extend(paths)

    print(f"\n{len(written)} file(s) written to {o_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
