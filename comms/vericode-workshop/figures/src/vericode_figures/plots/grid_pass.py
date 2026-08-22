"""Figure (a): grouped bars, macro PASS per model x mode, with bootstrap CI error bars."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..data import load_all_aggregates
from ..style import (
    MODE_LABELS,
    MODE_ORDER,
    MODEL_COLORS,
    MODEL_LABELS,
    MODEL_ORDER,
    new_figure,
    save_figure,
    set_rcparams,
)


def render(data_dir: Path, out_dir: Path) -> list[Path]:
    set_rcparams()
    aggregates = load_all_aggregates(data_dir)

    n_models = len(MODEL_ORDER)
    n_modes = len(MODE_ORDER)
    group_width = 0.8
    bar_width = group_width / n_models
    x = np.arange(n_modes)

    fig, ax = new_figure()
    for i, model in enumerate(MODEL_ORDER):
        rates = []
        lo_err = []
        hi_err = []
        for mode in MODE_ORDER:
            rec = aggregates[model][mode]
            rate = rec["macro_rate"]
            rates.append(rate)
            lo_err.append(rate - rec["macro_ci_lo"])
            hi_err.append(rec["macro_ci_hi"] - rate)
        offsets = x - group_width / 2 + bar_width * (i + 0.5)
        ax.bar(
            offsets,
            [r * 100 for r in rates],
            width=bar_width * 0.92,
            color=MODEL_COLORS[model],
            label=MODEL_LABELS[model],
            yerr=[[e * 100 for e in lo_err], [e * 100 for e in hi_err]],
            error_kw={"elinewidth": 0.7, "capsize": 2, "capthick": 0.7},
        )

    ax.set_xticks(x)
    ax.set_xticklabels([MODE_LABELS[m] for m in MODE_ORDER])
    ax.set_ylabel("macro PASS (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right", ncol=1)
    ax.set_title("macro PASS by model and mode")

    return save_figure(fig, out_dir / "grid-pass")
