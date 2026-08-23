"""Figure (f): PASS vs deletion depth (#133), macro rate with bootstrap CI whiskers,
plus the independence-null reference p1^N drawn from the depth-1 micro rate.

pipeline/deletion_curve.tsv may not exist on older checkouts; like budget_curve, this
prints a skip message and renders nothing rather than failing, so the same `uv run
figures` invocation produces the figure wherever the TSV is present.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..style import MODEL_COLORS, new_figure, save_figure, set_rcparams

_MODEL = "claude-sonnet-5"  # the sweep's one model; color kept consistent with the grid


def render(pipeline_dir: Path, out_dir: Path) -> list[Path]:
    tsv_path = pipeline_dir / "deletion_curve.tsv"
    if not tsv_path.exists():
        print(
            "  skipping deletion-curve: pipeline/deletion_curve.tsv not found "
            "(issue #133) — will render automatically once it lands"
        )
        return []

    rows = []
    with tsv_path.open(newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            rows.append(
                {
                    "depth": float(row["depth"]),
                    "micro": float(row["micro_rate"]),
                    "macro": float(row["macro_rate"]),
                    "lo": float(row["macro_ci_lo"]),
                    "hi": float(row["macro_ci_hi"]),
                }
            )
    rows.sort(key=lambda r: r["depth"])

    set_rcparams()
    fig, ax = new_figure()
    depths = [r["depth"] for r in rows]
    ax.errorbar(
        depths,
        [r["macro"] * 100 for r in rows],
        yerr=[
            [(r["macro"] - r["lo"]) * 100 for r in rows],
            [(r["hi"] - r["macro"]) * 100 for r in rows],
        ],
        color=MODEL_COLORS[_MODEL],
        marker="o",
        markersize=3,
        linewidth=1.2,
        elinewidth=0.7,
        capsize=1.5,
        label="macro PASS",
    )
    # Independence null: solving a depth-N problem as N independent depth-1 problems.
    p1 = rows[0]["micro"]
    ax.plot(
        depths,
        [(p1 ** r["depth"]) * 100 for r in rows],
        color="0.45",
        linestyle=":",
        linewidth=1.0,
        label=f"independence null ($p_1^N$, $p_1$={p1:.2f})",
    )
    ax.set_xlabel("deleted lemmas per problem (depth $N$)")
    ax.set_ylabel("PASS (%)")
    ax.set_xticks(depths)
    ax.set_ylim(0, max(35.0, rows[0]["hi"] * 100 + 5))
    ax.legend(loc="best", fontsize=6)
    ax.set_title("PASS vs deletion depth")

    return save_figure(fig, out_dir / "deletion-curve")
