"""Figure (e): PASS / tamper / turn-limit fraction vs turn budget, per model x mode.

pipeline/budget_curve.tsv does not exist yet (issue #131, in flight). This renders
nothing and prints a skip message when the file is absent, so the SAME `uv run figures`
invocation starts producing this figure the moment the file lands — no code change
needed on the day #131 ships.

Expected (approximate, per the brief) columns: model, mode, budget, pass, tamper,
turn_limit. Parsing below tolerates any column *order* and ignores extra columns, but
does require at least `model`, `budget`, and `pass` to be present.
"""

from __future__ import annotations

from pathlib import Path

from ..data import load_budget_curve
from ..style import (
    MODE_LABELS,
    MODEL_COLORS,
    MODEL_LABELS,
    MODEL_ORDER,
    TSV_MODEL_ALIASES,
    new_figure,
    save_figure,
    set_rcparams,
)

_LINESTYLES = {"leaves": "-", "easy": "-", "whole": "--", "hard": "--"}


def _canonical_model(name: str) -> str | None:
    if name in MODEL_ORDER:
        return name
    return TSV_MODEL_ALIASES.get(name)


def render(pipeline_dir: Path, out_dir: Path) -> list[Path]:
    rows = load_budget_curve(pipeline_dir)
    if rows is None:
        print(
            "  skipping budget-curve: pipeline/budget_curve.tsv not found "
            "(issue #131 in flight) — will render automatically once it lands"
        )
        return []

    set_rcparams()
    # y is the macro (repo-averaged) PASS rate — `pass` in the TSV is a raw COUNT, and
    # plotting it as a rate silently draws every line above the axis (the empty-plot bug
    # caught on first render). Prefer macro_rate + its bootstrap CI; fall back to
    # pass/scorable only if the rate columns are ever absent.
    by_model_mode: dict[tuple[str, str], list[tuple[float, float, float, float]]] = {}
    for row in rows:
        canonical = _canonical_model(row.get("model", ""))
        if canonical is None:
            continue
        mode = row.get("mode", "")
        if "macro_rate" in row:
            rate = row["macro_rate"]
            lo = row.get("macro_ci_lo", rate)
            hi = row.get("macro_ci_hi", rate)
        else:
            rate = row["pass"] / row["scorable"]
            lo = hi = rate
        by_model_mode.setdefault((canonical, mode), []).append(
            (row["budget"], rate, lo, hi)
        )

    fig, ax = new_figure()
    for model in MODEL_ORDER:
        modes_present = sorted({mode for (m, mode) in by_model_mode if m == model})
        for mode in modes_present:
            points = sorted(by_model_mode[(model, mode)], key=lambda p: p[0])
            budgets = [p[0] for p in points]
            passes = [p[1] * 100 for p in points]
            err_lo = [(p[1] - p[2]) * 100 for p in points]
            err_hi = [(p[3] - p[1]) * 100 for p in points]
            ax.errorbar(
                budgets,
                passes,
                yerr=[err_lo, err_hi],
                color=MODEL_COLORS[model],
                linestyle=_LINESTYLES.get(mode, "-"),
                marker="o",
                markersize=3,
                linewidth=1.2,
                elinewidth=0.7,
                capsize=1.5,
                label=f"{MODEL_LABELS[model]} ({MODE_LABELS.get(mode, mode)})",
            )

    ax.set_xlabel("turn budget")
    ax.set_ylabel("macro PASS (%)")
    ax.set_ylim(0, 100)
    ax.legend(loc="best", fontsize=6)
    ax.set_title("PASS vs turn budget")

    return save_figure(fig, out_dir / "budget-curve")
