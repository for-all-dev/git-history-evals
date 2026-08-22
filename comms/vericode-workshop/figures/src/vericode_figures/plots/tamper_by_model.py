"""Figure (c): tampered share of scorable rows per model — the reward-hacking headline.

No bootstrap CI here: unlike macro_rate, `ablate-aggregate` does not currently emit a CI
for the tampered fraction, and per "CIs always shown where available" we show one only
where the upstream data actually provides it.
"""

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
        shares = []
        for mode in MODE_ORDER:
            rec = aggregates[model][mode]
            shares.append(rec["outcomes"]["tampered"] / rec["scorable"])
        offsets = x - group_width / 2 + bar_width * (i + 0.5)
        ax.bar(
            offsets,
            [s * 100 for s in shares],
            width=bar_width * 0.92,
            color=MODEL_COLORS[model],
            label=MODEL_LABELS[model],
        )

    ax.set_xticks(x)
    ax.set_xticklabels([MODE_LABELS[m] for m in MODE_ORDER])
    ax.set_ylabel("tampered / scorable (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="upper right")
    ax.set_title("tamper rate by model and mode")

    return save_figure(fig, out_dir / "tamper-by-model")
