"""Per-challenge feature extraction from an enriched ablator JSONL record.

The extractor reads the *raw* record dict (not the lossy `AblationRecord` pydantic
model, which drops the per-hole / per-deleted-lemma metrics we need). Every structural
metric comes straight from the fields the ablators emit; Python only aggregates them
across the (possibly several) deleted lemmas / holes / corollaries with sum/max/mean,
and measures a couple of trivially language-agnostic text sizes.

Legacy datasets predate the metric fields; anything missing degrades to `None`, and an
aggregate over an all-missing list is `None`. See `docs/difficulty-features.md` §3.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Metric fields carried by each deleted-lemma / hole / corollary object, aggregated.
#
# The size/shape metrics (n_lines, n_chars, n_tactics, cyclomatic) turned out to be weak
# predictors on their own (ROC-AUC 0.61): they cannot tell a `by simp` one-liner from a 40-line
# induction with the same step count. The ablators now also emit what the proof *does*
# (n_automation / n_rewrites / n_structural / automation_only / max_nesting) and how much the
# corollary rests on (n_deps_direct / n_deps_transitive) — see docs/difficulty-features.md.
_PROOF_CHARACTER = (
    "n_automation",
    "n_rewrites",
    "n_structural",
    "automation_only",  # bool -> 0/1; the "closable by one tactic call" class
    "max_nesting",
)
_DELETED_METRICS = (
    "fan_in",
    "n_lines",
    "n_chars",
    "n_subproofs",
    "n_tactics",
    "cyclomatic",
    *_PROOF_CHARACTER,
)
_HOLE_METRICS = (
    "n_lines",
    "n_commands",
    "n_chars",
    "n_subproofs",
    "n_tactics",
    "cyclomatic",
    "centrality",
    "depth",
    *_PROOF_CHARACTER,
)
_COROLLARY_METRICS = (
    "fan_in",
    "n_lines",
    "n_subproofs",
    "n_tactics",
    "cyclomatic",
    # how many in-file lemmas the ablated corollary rests on (the deleted lemma is one)
    "n_deps_direct",
    "n_deps_transitive",
    *_PROOF_CHARACTER,
)


def _n_lines(text: str) -> int:
    return 0 if not text else text.count("\n") + 1


def _num(x: Any) -> float | int | None:
    """Coerce an emitted metric to a number, or None if absent/non-numeric."""
    if isinstance(x, bool):  # guard: bool is a subclass of int
        return int(x)
    if isinstance(x, (int, float)):
        return x
    return None


def _agg(objs: Iterable[dict[str, Any]], field: str, prefix: str) -> dict[str, Any]:
    """sum/max/mean of `obj[field]` over `objs`, skipping missing values."""
    vals = [n for o in objs if (n := _num(o.get(field))) is not None]
    if not vals:
        return {f"{prefix}_sum": None, f"{prefix}_max": None, f"{prefix}_mean": None}
    return {
        f"{prefix}_sum": sum(vals),
        f"{prefix}_max": max(vals),
        f"{prefix}_mean": sum(vals) / len(vals),
    }


def _window(spec_val: Any) -> int | None:
    """Knob windows may be the sentinel string 'inf'; keep ints, map 'inf'/None to None."""
    return (
        spec_val
        if isinstance(spec_val, int) and not isinstance(spec_val, bool)
        else None
    )


def extract_features(record: dict[str, Any]) -> dict[str, Any]:
    """Flatten one raw ablation record into a fixed-key feature dict.

    Identity/knob columns (task_id, seed, ...) are included for slicing but are not
    intended as predictive features; the classifier layer decides what to consume.
    """
    holes: list[dict[str, Any]] = record.get("holes_filled") or []
    deleted: list[dict[str, Any]] = record.get("deleted_lemmas") or []
    corollaries: list[dict[str, Any]] = record.get("corollaries") or []
    challenge = record.get("challenge_file_content") or ""
    solution = record.get("solution_file_content") or ""

    feats: dict[str, Any] = {}

    # --- identity / join columns ---
    feats["challenge_id"] = record.get("challenge_id")
    feats["task_id"] = record.get("task_id")
    feats["file_path"] = record.get("file_path")
    feats["proof_assistant"] = record.get("proof_assistant")
    # Which ablation mode this challenge was drawn under ("leaves" / "whole"), when the
    # source challenges.jsonl was produced by sample_disjoint.py / sample_paired.py. Not a
    # model feature (excluded from DEFAULT_FEATURES) -- it's a join-disambiguation column:
    # challenge_id does NOT encode mode (see ablators/lean/Ablator/Record.lean:87-96), so a
    # paired easy/hard sample shares challenge_id across modes and needs `sample_mode` to
    # join to the right result row.
    feats["sample_mode"] = record.get("sample_mode")

    # --- counts ---
    feats["n_proofs"] = _num(record.get("n_proofs"))
    feats["n_ablated"] = _num(record.get("n_ablated"))
    feats["n_holes"] = len(holes)
    feats["n_deleted_lemmas"] = len(deleted)
    feats["n_corollaries"] = len(corollaries)
    feats["closure_size"] = _num(record.get("closure_size"))

    # --- sizes (language-agnostic text measures) ---
    ch_lines, sol_lines = _n_lines(challenge), _n_lines(solution)
    feats["challenge_n_lines"] = ch_lines
    feats["challenge_n_chars"] = len(challenge)
    feats["solution_n_lines"] = sol_lines
    feats["solution_n_chars"] = len(solution)
    # net lines the solution adds back (answer size); None if no whole-file solution
    feats["solution_minus_challenge_lines"] = (
        (sol_lines - ch_lines) if solution else None
    )

    # --- deleted-lemma aggregates ---
    for field in _DELETED_METRICS:
        feats.update(_agg(deleted, field, f"del_{field}"))

    # --- hole aggregates ---
    for field in _HOLE_METRICS:
        feats.update(_agg(holes, field, f"hole_{field}"))
    feats["hole_n_leaves"] = sum(1 for h in holes if h.get("is_leaf"))

    # --- corollary aggregates ---
    for field in _COROLLARY_METRICS:
        feats.update(_agg(corollaries, field, f"cor_{field}"))

    # --- knobs / metadata ---
    feats["challenge_type"] = record.get("challenge_type")
    feats["by_centrality"] = (
        bool(record.get("by_centrality")) if "by_centrality" in record else None
    )
    feats["leaves_only"] = (
        bool(record.get("leaves_only")) if "leaves_only" in record else None
    )
    feats["ablation_prob"] = _num(record.get("ablation_prob"))
    feats["min_depth"] = _window(record.get("min_depth"))
    feats["max_depth"] = _window(record.get("max_depth"))
    feats["min_size"] = _window(record.get("min_size"))
    feats["max_size"] = _window(record.get("max_size"))
    feats["min_centrality"] = _window(record.get("min_centrality"))
    feats["max_centrality"] = _window(record.get("max_centrality"))
    feats["seed"] = _num(record.get("seed"))

    return feats


def _reference_keys() -> list[str]:
    """The fixed, ordered feature-key list (columns), derived from an empty record so
    every extracted row shares the same schema regardless of content."""
    return list(extract_features({}).keys())


# Stable column order for tables. extract_features always inserts keys in this order.
FEATURE_KEYS: list[str] = _reference_keys()
