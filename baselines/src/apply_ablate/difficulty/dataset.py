"""Join challenge features to harness outcomes and emit a training table.

The join prefers the stable `challenge_id` present on both the enriched record and the
`SolveResult` (see `docs/difficulty-features.md` §4). For legacy runs that predate
`challenge_id`, it falls back to positional line-order (the harness writes results in
challenge order), cross-checking `task_id`/`file_path` so silent misalignment surfaces.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from apply_ablate.difficulty.features import FEATURE_KEYS, extract_features
from apply_ablate.difficulty.label import is_trainable, label_of, outcome_of

# columns appended to the feature columns when labels are joined
LABEL_KEYS = ["outcome", "label", "trainable", "matched_by"]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load non-blank JSONL rows as dicts (blank padding lines skipped)."""
    rows: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            rows.append(json.loads(ln))
    return rows


def extract_all(challenges: Path) -> list[dict[str, Any]]:
    """Feature rows for every challenge (no labels)."""
    return [extract_features(rec) for rec in load_jsonl(challenges)]


def _cross_check(chal: dict[str, Any], res: dict[str, Any]) -> bool:
    """Positional fallback sanity: task_id or file_path should agree when both present."""
    for key in ("task_id", "file_path"):
        a, b = chal.get(key), res.get(key)
        if a is not None and b is not None and a != b:
            return False
    return True


def build_table(
    challenges: Path, results: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    """Join features to labels. Returns (rows, warnings)."""
    chal_recs = load_jsonl(challenges)
    res_recs = load_jsonl(results)
    by_cid = {r["challenge_id"]: r for r in res_recs if r.get("challenge_id")}

    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    for i, chal in enumerate(chal_recs):
        cid = chal.get("challenge_id")
        res: dict[str, Any] | None = None
        matched_by: str | None = None
        if cid and cid in by_cid:
            res, matched_by = by_cid[cid], "challenge_id"
        elif i < len(res_recs):
            res, matched_by = res_recs[i], "position"
            if not _cross_check(chal, res):
                warnings.append(
                    f"row {i}: positional join mismatch "
                    f"(challenge task_id={chal.get('task_id')!r} file={chal.get('file_path')!r} "
                    f"vs result task_id={res.get('task_id')!r} file={res.get('file_path')!r})"
                )
                res, matched_by = None, None

        row = extract_features(chal)
        if res is None:
            row.update(
                {"outcome": None, "label": None, "trainable": None, "matched_by": None}
            )
            warnings.append(f"row {i}: no matching result (challenge_id={cid!r})")
        else:
            row.update(
                {
                    "outcome": outcome_of(res),
                    "label": label_of(res),
                    "trainable": is_trainable(res),
                    "matched_by": matched_by,
                }
            )
        rows.append(row)
    return rows, warnings


def write_jsonl(rows: list[dict[str, Any]], out: Path) -> None:
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def write_csv(rows: list[dict[str, Any]], out: Path, *, with_labels: bool) -> None:
    cols = list(FEATURE_KEYS) + (LABEL_KEYS if with_labels else [])
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
