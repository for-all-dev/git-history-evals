"""Load the committed/symlinked figure inputs.

Every loader here is a plain, deterministic file read — no randomness, no
network. The aggregate JSONs are read through symlinks into a gitignored
scratch tree (see comms/vericode-workshop/data/README.md); a dangling
symlink is a real error, not a thing to silently skip, so it's reported
clearly rather than surfacing as an opaque FileNotFoundError deep in json
parsing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .style import MODEL_ORDER

AGGREGATE_FILENAMES = {
    "claude-sonnet-5": "aggregate-claude-sonnet-5.json",
    "openai:gpt-5.6-sol": "aggregate-gpt-5.6-sol.json",
    "mistral:labs-leanstral-1-5": "aggregate-leanstral-1-5.json",
}


class MissingAggregateError(RuntimeError):
    pass


def load_aggregate(data_dir: Path, model: str) -> dict[str, dict[str, Any]]:
    """Return {mode: record} for one model's aggregate.json symlink.

    Raises MissingAggregateError with an actionable message if the symlink is
    missing or dangling (e.g. scratch-wave3/ was never populated by an eval run).
    """
    filename = AGGREGATE_FILENAMES[model]
    link_path = data_dir / filename
    if not link_path.exists():
        target = link_path.resolve() if link_path.is_symlink() else link_path
        raise MissingAggregateError(
            f"aggregate missing — run the evals or fetch results\n"
            f"  expected: {link_path}\n"
            f"  resolves to: {target}\n"
            f"  This is a symlink into scratch-wave3/ (gitignored). Either rerun the "
            f"paired-sample eval that produces scratch-wave3/{{paired,paired-openai,"
            f"paired-leanstral}}/aggregate.json (see scratch-wave3/GRID.md), or fetch a "
            f"mirrored copy of scratch-wave3/ and place it at the repo root."
        )
    records = json.loads(link_path.read_text())
    return {rec["mode"]: rec for rec in records}


def load_all_aggregates(data_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """{model: {mode: record}} for every model in MODEL_ORDER, in that fixed order."""
    return {model: load_aggregate(data_dir, model) for model in MODEL_ORDER}


def load_temporal_holdout(pipeline_dir: Path) -> list[dict[str, Any]]:
    tsv_path = pipeline_dir / "temporal_holdout.tsv"
    if not tsv_path.exists():
        raise MissingAggregateError(
            f"expected {tsv_path} (committed on master) — not found"
        )
    rows: list[dict[str, Any]] = []
    with tsv_path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            rows.append(
                {
                    "model": row["model"],
                    "mode": row["mode"],
                    "cutoff": row["cutoff"],
                    "pre_n": int(row["pre_n"]),
                    "pre_macro": float(row["pre_macro"]),
                    "post_n": int(row["post_n"]),
                    "post_macro": float(row["post_macro"]),
                }
            )
    return rows


def load_budget_curve(pipeline_dir: Path) -> list[dict[str, Any]] | None:
    """Return parsed rows, or None if pipeline/budget_curve.tsv doesn't exist yet (#131)."""
    tsv_path = pipeline_dir / "budget_curve.tsv"
    if not tsv_path.exists():
        return None
    rows: list[dict[str, Any]] = []
    with tsv_path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            parsed = dict(row)
            for key in ("budget",):
                if key in parsed:
                    parsed[key] = float(parsed[key])
            for key in ("pass", "tamper", "turn_limit"):
                if key in parsed:
                    parsed[key] = float(parsed[key])
            rows.append(parsed)
    return rows
