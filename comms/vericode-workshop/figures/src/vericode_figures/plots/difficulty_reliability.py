"""Appendix figure: decile reliability diagram for the per-agent difficulty models of
\\S\\ref{sec:difficulty}, companion to the Brier/Murphy decomposition in
Table~\\ref{tab:difficulty}.

Reads the same pipeline/difficulty_predictions.tsv as difficulty_roc.py. Bins are equal
-width deciles of predicted P(pass) in [0, 1] (matching the reliability/Murphy
computation used for the table), not equal-count bins, so an empty decile is dropped
rather than merged.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..style import MODEL_COLORS, MODEL_LABELS, new_figure, save_figure, set_rcparams


def _read_predictions(tsv_path: Path) -> dict[str, list[tuple[float, int]]]:
    by_agent: dict[str, list[tuple[float, int]]] = {}
    with tsv_path.open(newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            by_agent.setdefault(row["agent"], []).append(
                (float(row["p_pass"]), int(row["label"]))
            )
    return by_agent


def _deciles(pairs: list[tuple[float, int]]) -> tuple[list[float], list[float], list[int]]:
    bins: dict[int, list[tuple[float, int]]] = {}
    for p, y in pairs:
        k = min(9, int(p * 10))
        bins.setdefault(k, []).append((p, y))
    mean_pred, obs_freq, counts = [], [], []
    for k in sorted(bins):
        v = bins[k]
        mean_pred.append(sum(p for p, _ in v) / len(v))
        obs_freq.append(sum(y for _, y in v) / len(v))
        counts.append(len(v))
    return mean_pred, obs_freq, counts


def render(pipeline_dir: Path, out_dir: Path) -> list[Path]:
    tsv_path = pipeline_dir / "difficulty_predictions.tsv"
    if not tsv_path.exists():
        print(
            "  skipping difficulty-reliability: pipeline/difficulty_predictions.tsv not "
            "found (sec:difficulty held-out predictions) — will render once it lands"
        )
        return []

    by_agent = _read_predictions(tsv_path)

    set_rcparams()
    fig, ax = new_figure()
    ax.plot([0, 1], [0, 1], color="0.6", linestyle=":", linewidth=1.0, label="perfect calibration")
    for agent, pairs in sorted(by_agent.items(), key=lambda kv: -len(kv[1])):
        mean_pred, obs_freq, counts = _deciles(pairs)
        sizes = [12 + 3 * c for c in counts]
        label = MODEL_LABELS.get(agent, agent)
        color = MODEL_COLORS.get(agent, "0.3")
        ax.plot(mean_pred, obs_freq, color=color, linewidth=1.0, zorder=2)
        ax.scatter(mean_pred, obs_freq, s=sizes, color=color, label=label, zorder=3)
    ax.set_xlabel("mean predicted P(pass), decile bin")
    ax.set_ylabel("observed pass rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.legend(loc="upper left", fontsize=6)
    ax.set_title("Held-out difficulty model: reliability")

    return save_figure(fig, out_dir / "difficulty-reliability")
