"""Logistic-regression difficulty model over the feature table.

`difficulty = 1 - P(success)`: we fit a regularised logistic regression that predicts
whether the baseline agent solves a challenge, and read its failure probability as the
difficulty score. Kept deliberately simple and interpretable; the harder modelling
(GBM, per-model calibration, more data) is future work.

Small-n reality: a single harness run yields tens of labelled rows, so we (a) restrict
to a curated numeric feature subset, (b) impute missing metrics (legacy rows) with the
median, (c) L2-regularise, (d) balance classes, and (e) report cross-validated ROC-AUC
with an honest guard when there is too little data / a single class to score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Curated numeric predictors (exclude identity/knob/label columns). Kept compact to
# limit overfitting on small label sets; override via `feature_names=`.
#
# STYLE-INVARIANT by design: these are proof-complexity metrics of the deleted lemma /
# holes / corollary plus structural counts — NOT the absolute challenge/solution FILE
# sizes. The file-size features (`challenge_n_lines`, `solution_n_lines`,
# `solution_minus_challenge_lines`) depend on the ablation STYLE (whole-file vs
# minimal-shrink), so a model trained with them does not transfer across datasets mined
# differently — and, empirically, they were mild overfitting even in-repo (dropping them
# nudged cedar CV-AUC 0.75 -> 0.763). They live in the feature table for slicing but are
# deliberately excluded from the model. See docs/difficulty-features.md.
DEFAULT_FEATURES: list[str] = [
    "n_holes",
    "n_deleted_lemmas",
    "n_corollaries",
    "closure_size",
    "del_fan_in_sum",
    "del_fan_in_max",
    "del_n_lines_sum",
    "del_n_subproofs_sum",
    "del_n_tactics_sum",
    "del_cyclomatic_sum",
    "del_cyclomatic_max",
    "hole_n_lines_sum",
    "hole_n_subproofs_sum",
    "hole_n_tactics_sum",
    "hole_cyclomatic_sum",
    "hole_cyclomatic_max",
    "hole_centrality_max",
    "hole_depth_max",
    "hole_n_leaves",
    "cor_cyclomatic_max",
    "cor_n_tactics_max",
    "cor_fan_in_max",
]


@dataclass
class DifficultyModel:
    """A fitted pipeline plus the columns it consumes and its fit diagnostics."""

    pipeline: Pipeline
    feature_names: list[str]  # predictors the model actually consumes
    dropped_features: list[str]  # requested predictors dropped as all-missing
    n_train: int
    pos_rate: float
    cv_auc: float | None  # cross-validated ROC-AUC, or None if not scorable

    def score(self, rows: list[dict[str, Any]]) -> list[float]:
        """Difficulty (= P(fail)) for feature rows produced by extract_features."""
        x = _matrix(rows, self.feature_names)
        p_success = self.pipeline.predict_proba(x)[:, 1]
        return [float(1.0 - p) for p in p_success]


def _num(v: Any) -> float:
    """Coerce a cell to float, mapping missing/non-numeric to NaN for imputation."""
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return np.nan
    return np.nan


def _matrix(rows: list[dict[str, Any]], feature_names: list[str]) -> np.ndarray:
    return np.array(
        [[_num(r.get(c)) for c in feature_names] for r in rows], dtype=float
    )


def _labels(rows: list[dict[str, Any]]) -> np.ndarray:
    return np.array([int(r["label"]) for r in rows], dtype=int)


def trainable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows usable for training: joined to a label and not trivial/malformed/dry_run."""
    return [r for r in rows if r.get("label") is not None and r.get("trainable")]


class NotEnoughData(ValueError):
    """Raised when the labelled set cannot support a classifier (too few / one class)."""


def train(
    rows: list[dict[str, Any]],
    *,
    feature_names: list[str] | None = None,
    C: float = 0.5,
    min_n: int = 20,
) -> DifficultyModel:
    """Fit the difficulty model on already-trainable rows.

    Raises NotEnoughData if there are fewer than `min_n` rows or only one class.
    """
    feats = list(feature_names or DEFAULT_FEATURES)
    y = _labels(rows)
    n = len(y)
    if n < min_n:
        raise NotEnoughData(
            f"only {n} labelled rows (need >= {min_n}); run more challenges first"
        )
    classes, counts = np.unique(y, return_counts=True)
    if len(classes) < 2:
        raise NotEnoughData(
            f"all {n} labels are the same class ({classes.tolist()}); "
            "a classifier needs both PASS and FAIL examples"
        )

    # Drop predictors that are entirely missing across the training set (e.g. legacy
    # rows lacking the enriched metrics) so feature_names reflects what is actually used.
    x_full = _matrix(rows, feats)
    keep = [j for j in range(x_full.shape[1]) if not np.isnan(x_full[:, j]).all()]
    dropped = [feats[j] for j in range(len(feats)) if j not in keep]
    feats = [feats[j] for j in keep]
    if not feats:
        raise NotEnoughData(
            "no usable (non-empty) feature columns in the training rows"
        )

    x = x_full[:, keep]
    pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
            ("lr", LogisticRegression(C=C, class_weight="balanced", max_iter=1000)),
        ]
    )

    # Cross-validated AUC as an honest read on signal; guarded for tiny minority classes.
    cv_auc: float | None = None
    n_splits = int(min(5, counts.min()))
    if n_splits >= 2:
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=0)
        try:
            cv_auc = float(
                cross_val_score(pipeline, x, y, cv=skf, scoring="roc_auc").mean()
            )
        except ValueError:
            cv_auc = None

    pipeline.fit(x, y)
    return DifficultyModel(
        pipeline=pipeline,
        feature_names=feats,
        dropped_features=dropped,
        n_train=n,
        pos_rate=float(y.mean()),
        cv_auc=cv_auc,
    )


def save(model: DifficultyModel, path: Any) -> None:
    import joblib

    joblib.dump(model, path)


def load(path: Any) -> DifficultyModel:
    import joblib

    return joblib.load(path)
