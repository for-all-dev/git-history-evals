"""Appendix figure: held-out ROC curves for the per-agent difficulty models of
\\S\\ref{sec:difficulty}.

Reads pipeline/difficulty_predictions.tsv: one row per (agent, held-out challenge_id)
with the model's P(pass) and the observed label. The three agents, their held-out
sample, and the ROC-AUC values are exactly Table~\\ref{tab:difficulty}; this figure is
the curve behind that table's scalar, not a new measurement.

Leanstral is absent from the table (rate-limited, incomplete at submission) and
therefore has no row here either.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from ..style import MODEL_COLORS, MODEL_LABELS, new_figure, save_figure, set_rcparams


def _read_predictions(tsv_path: Path) -> dict[str, list[tuple[float, int]]]:
    by_agent: dict[str, list[tuple[float, int]]] = {}
    with tsv_path.open(newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            by_agent.setdefault(row["agent"], []).append(
                (float(row["p_pass"]), int(row["label"]))
            )
    return by_agent


def _roc_curve(scores: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """FPR, TPR at every distinct threshold, plus the trapezoidal AUC."""
    order = np.argsort(-scores, kind="mergesort")
    scores, labels = scores[order], labels[order]
    n_pos = labels.sum()
    n_neg = len(labels) - n_pos
    tps = np.cumsum(labels)
    fps = np.cumsum(1 - labels)
    # Collapse ties: keep only the point after the last occurrence of each score.
    distinct = np.r_[np.where(np.diff(scores) != 0)[0], len(scores) - 1]
    tpr = np.r_[0.0, tps[distinct] / n_pos]
    fpr = np.r_[0.0, fps[distinct] / n_neg]
    auc = float(np.trapezoid(tpr, fpr))
    return fpr, tpr, auc


def render(pipeline_dir: Path, out_dir: Path) -> list[Path]:
    tsv_path = pipeline_dir / "difficulty_predictions.tsv"
    if not tsv_path.exists():
        print(
            "  skipping difficulty-roc: pipeline/difficulty_predictions.tsv not found "
            "(sec:difficulty held-out predictions) — will render once it lands"
        )
        return []

    by_agent = _read_predictions(tsv_path)

    set_rcparams()
    fig, ax = new_figure()
    ax.plot([0, 1], [0, 1], color="0.6", linestyle=":", linewidth=1.0, label="chance")
    for agent, pairs in sorted(by_agent.items(), key=lambda kv: -len(kv[1])):
        scores = np.array([p for p, _ in pairs])
        labels = np.array([y for _, y in pairs])
        fpr, tpr, auc = _roc_curve(scores, labels)
        label = MODEL_LABELS.get(agent, agent)
        ax.plot(
            fpr,
            tpr,
            color=MODEL_COLORS.get(agent, "0.3"),
            linewidth=1.3,
            label=f"{label} (AUC {auc:.3f})",
        )
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.legend(loc="lower right", fontsize=6)
    ax.set_title("Held-out difficulty model: ROC")

    return save_figure(fig, out_dir / "difficulty-roc")
