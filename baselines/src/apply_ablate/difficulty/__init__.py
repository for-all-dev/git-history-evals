"""Difficulty features & label plumbing for ablation challenges.

This subpackage turns the enriched ablator JSONL records (see
`docs/difficulty-features.md`) into a per-challenge feature vector, and joins those
features to the `ablate-baseline` harness outcomes to produce a training table for the
difficulty classifier — a regularised logistic regression that reads `1 - P(success)`
as a difficulty score (see `model.py`).

The proof-complexity metrics themselves (tactic/sub-proof/cyclomatic counts, fan-in)
are computed *inside* the four ablators so they are visible in the JSONL and on any
website; the Python side only reads and aggregates them.
"""

from apply_ablate.difficulty.features import FEATURE_KEYS, extract_features
from apply_ablate.difficulty.label import is_trainable, label_of, outcome_of

__all__ = [
    "extract_features",
    "FEATURE_KEYS",
    "outcome_of",
    "label_of",
    "is_trainable",
]
