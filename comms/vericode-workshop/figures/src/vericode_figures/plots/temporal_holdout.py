"""Figure (d): pre vs post macro PASS per model per cutoff, from pipeline/temporal_holdout.tsv.

pre-side n is small (10-19) — annotated honestly on the x-axis rather than left implicit,
per the brief. n depends only on (mode, cutoff), not model, so it's annotated once per
cutoff group rather than repeated per model.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from ..data import load_temporal_holdout
from ..style import (
    MODEL_COLORS,
    MODEL_LABELS,
    MODEL_ORDER,
    TSV_MODEL_ALIASES,
    save_figure,
    set_rcparams,
)

_FACET_ORDER = ["easy", "hard"]
_TSV_NAME_BY_CANONICAL = {v: k for k, v in TSV_MODEL_ALIASES.items()}


def render(pipeline_dir: Path, out_dir: Path) -> list[Path]:
    set_rcparams()
    rows = load_temporal_holdout(pipeline_dir)

    cutoffs = sorted({row["cutoff"] for row in rows})
    by_key = {(r["model"], r["mode"], r["cutoff"]): r for r in rows}

    n_models = len(MODEL_ORDER)
    jitter = 0.09
    offsets = [(i - (n_models - 1) / 2) * jitter for i in range(n_models)]

    fig, axes = plt.subplots(
        len(_FACET_ORDER), 1, figsize=(3.4, 3.8), sharex=True, sharey=True
    )

    for ax, mode in zip(axes, _FACET_ORDER):
        for i, model in enumerate(MODEL_ORDER):
            tsv_name = _TSV_NAME_BY_CANONICAL[model]
            color = MODEL_COLORS[model]
            for c_idx, cutoff in enumerate(cutoffs):
                row = by_key[(tsv_name, mode, cutoff)]
                x = c_idx + offsets[i]
                pre_pct = row["pre_macro"] * 100
                post_pct = row["post_macro"] * 100
                ax.plot(
                    [x, x], [pre_pct, post_pct], color=color, linewidth=0.9, zorder=2
                )
                ax.plot(
                    x,
                    pre_pct,
                    marker="o",
                    markerfacecolor="white",
                    markeredgecolor=color,
                    markeredgewidth=1.1,
                    markersize=4,
                    zorder=3,
                )
                ax.plot(
                    x,
                    post_pct,
                    marker="o",
                    markerfacecolor=color,
                    markeredgecolor=color,
                    markersize=4,
                    zorder=3,
                )
        ax.set_ylabel(f"{mode}\nmacro PASS (%)")
        ax.set_ylim(0, 100)

    tick_labels = []
    for cutoff in cutoffs:
        sample_row = next(r for r in rows if r["cutoff"] == cutoff)
        tick_labels.append(
            f"{cutoff}\n(pre n={sample_row['pre_n']}, post n={sample_row['post_n']})"
        )
    axes[-1].set_xticks(range(len(cutoffs)))
    axes[-1].set_xticklabels(tick_labels)
    axes[-1].set_xlim(-0.5, len(cutoffs) - 0.5)

    model_handles = [
        Line2D([0], [0], color=MODEL_COLORS[m], linewidth=2, label=MODEL_LABELS[m])
        for m in MODEL_ORDER
    ]
    marker_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="white",
            markeredgecolor="black",
            markeredgewidth=1.1,
            markersize=4,
            label="pre-cutoff",
        ),
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="none",
            markerfacecolor="black",
            markeredgecolor="black",
            markersize=4,
            label="post-cutoff",
        ),
    ]
    fig.legend(
        handles=model_handles + marker_handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.06),
        ncol=3,
        columnspacing=1.0,
        handlelength=1.4,
        fontsize=6.5,
    )
    fig.suptitle(
        "temporal holdout: pre- vs post-cutoff macro PASS", y=1.16, fontsize=8.5
    )
    fig.tight_layout()

    return save_figure(fig, out_dir / "temporal-holdout")
