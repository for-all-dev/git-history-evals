"""Figure (b): per model x mode stacked bars over the outcome taxonomy.

pass/tampered/turn_limit/gave_up/error/fail stack to `scorable`'s share of `total`.
malformed (plus, if present, dry_run/trivial/context_exceeded — all excluded-from-
denominator outcomes) stacks on top, hatched, so the bar always sums to 100% of `total`
and the excluded slice reads as visually distinct from a real outcome.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.transforms as mtransforms

from ..data import load_all_aggregates
from ..style import (
    EXCLUDED_COLOR,
    EXCLUDED_HATCH,
    MODE_LABELS,
    MODE_ORDER,
    MODEL_LABELS,
    MODEL_ORDER,
    OUTCOME_COLORS,
    OUTCOME_LABELS,
    OUTCOME_ORDER,
    new_figure,
    save_figure,
    set_rcparams,
)

_OTHER_EXCLUDED_KEYS = ("dry_run", "trivial", "context_exceeded")


def render(data_dir: Path, out_dir: Path) -> list[Path]:
    set_rcparams()
    aggregates = load_all_aggregates(data_dir)

    n_modes = len(MODE_ORDER)
    group_gap = 1  # extra x-units of blank space between model groups
    bar_width = 0.7

    fig, ax = new_figure(height_in=2.6)

    positions: list[float] = []
    group_centers: list[float] = []
    for i, model in enumerate(MODEL_ORDER):
        group_start = i * (n_modes + group_gap)
        group_positions = [group_start + j for j in range(n_modes)]
        positions.extend(group_positions)
        group_centers.append(sum(group_positions) / n_modes)

    seen_labels: set[str] = set()
    excluded_labeled = False
    pos_idx = 0
    for model in MODEL_ORDER:
        for mode in MODE_ORDER:
            rec = aggregates[model][mode]
            total = rec["total"]
            outcomes = rec["outcomes"]
            x = positions[pos_idx]
            pos_idx += 1

            bottom = 0.0
            for outcome in OUTCOME_ORDER:
                frac = outcomes.get(outcome, 0) / total
                label = OUTCOME_LABELS[outcome] if outcome not in seen_labels else None
                ax.bar(
                    x,
                    frac * 100,
                    width=bar_width,
                    bottom=bottom * 100,
                    color=OUTCOME_COLORS[outcome],
                    label=label,
                    linewidth=0,
                )
                seen_labels.add(outcome)
                bottom += frac

            excluded = outcomes.get("malformed", 0) + sum(
                outcomes.get(k, 0) for k in _OTHER_EXCLUDED_KEYS
            )
            excluded_frac = excluded / total
            ax.bar(
                x,
                excluded_frac * 100,
                width=bar_width,
                bottom=bottom * 100,
                color=EXCLUDED_COLOR,
                hatch=EXCLUDED_HATCH,
                edgecolor="white",
                linewidth=0.3,
                label="excluded (malformed, …)" if not excluded_labeled else None,
            )
            excluded_labeled = True

    ax.set_xticks(positions)
    ax.set_xticklabels([MODE_LABELS[m] for m in MODE_ORDER] * len(MODEL_ORDER))
    ax.set_ylabel("share of challenges (%)")
    ax.set_ylim(0, 100)

    # A second row of labels naming the model each group of bars belongs to.
    trans = mtransforms.blended_transform_factory(ax.transData, ax.transAxes)
    for model, center in zip(MODEL_ORDER, group_centers):
        ax.text(
            center,
            -0.22,
            MODEL_LABELS[model],
            transform=trans,
            ha="center",
            va="top",
            fontsize=7,
        )

    ax.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, 1.32),
        ncol=3,
        columnspacing=1.0,
        handlelength=1.2,
    )
    fig.subplots_adjust(bottom=0.22, top=0.78)

    return save_figure(fig, out_dir / "outcome-composition")
