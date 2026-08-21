"""`difficulty` CLI: extract per-challenge features and build a features+label table.

    difficulty extract CHALLENGES [--out-jsonl F] [--out-csv F]
    difficulty build-table CHALLENGES RESULTS [--out-jsonl F] [--out-csv F]
    difficulty train TABLE.jsonl [--out model.joblib] [--C 0.5]
    difficulty score CHALLENGES --model model.joblib [--out-jsonl F]

`extract` reads an enriched ablator `challenges.jsonl` and emits one feature row per
challenge. `build-table` joins the `ablate-baseline` result JSONL to attach the PASS/FAIL
label + outcome class. `train` fits the logistic-regression difficulty model on that
labelled table; `score` attaches `difficulty = 1 - P(success)` to fresh challenges. See
docs/difficulty-features.md and `apply_ablate.difficulty.model`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from apply_ablate.difficulty import dataset

app = typer.Typer(add_completion=False, help=__doc__)


def _echo(msg: str) -> None:
    typer.echo(msg)


def _write(
    rows: list[dict], out_jsonl: Path | None, out_csv: Path | None, *, with_labels: bool
) -> None:
    if out_jsonl:
        dataset.write_jsonl(rows, out_jsonl)
        _echo(f"wrote {len(rows)} rows -> {out_jsonl}")
    if out_csv:
        dataset.write_csv(rows, out_csv, with_labels=with_labels)
        _echo(f"wrote {len(rows)} rows -> {out_csv}")
    if not out_jsonl and not out_csv:
        _echo("(no --out-jsonl/--out-csv given; nothing written)")


@app.command()
def extract(
    challenges: Annotated[
        Path, typer.Argument(help="enriched ablator challenges.jsonl")
    ],
    out_jsonl: Annotated[
        Path | None, typer.Option("--out-jsonl", help="feature rows as JSONL")
    ] = None,
    out_csv: Annotated[
        Path | None, typer.Option("--out-csv", help="feature rows as CSV")
    ] = None,
) -> None:
    """Extract per-challenge features (no labels)."""
    rows = dataset.extract_all(challenges)
    _echo(f"extracted features for {len(rows)} challenge(s)")
    _write(rows, out_jsonl, out_csv, with_labels=False)


@app.command("build-table")
def build_table(
    challenges: Annotated[
        Path, typer.Argument(help="enriched ablator challenges.jsonl")
    ],
    results: Annotated[Path, typer.Argument(help="ablate-baseline result JSONL")],
    out_jsonl: Annotated[
        Path | None, typer.Option("--out-jsonl", help="joined table as JSONL")
    ] = None,
    out_csv: Annotated[
        Path | None, typer.Option("--out-csv", help="joined table as CSV")
    ] = None,
) -> None:
    """Join features to harness outcomes into a features+label table."""
    rows, warnings = dataset.build_table(challenges, results)
    labelled = sum(1 for r in rows if r.get("label") is not None)
    trainable = sum(1 for r in rows if r.get("trainable"))
    passes = sum(1 for r in rows if r.get("label") == 1)
    _echo(
        f"joined {labelled}/{len(rows)} challenge(s); {trainable} trainable; {passes} PASS"
    )
    for w in warnings:
        _echo(f"  warn: {w}")
    _write(rows, out_jsonl, out_csv, with_labels=True)


@app.command()
def train(
    table: Annotated[
        Path, typer.Argument(help="build-table output (JSONL) with labels")
    ],
    out: Annotated[Path, typer.Option("--out", help="fitted model (.joblib)")] = Path(
        "difficulty-model.joblib"
    ),
    c: Annotated[
        float, typer.Option("--C", help="inverse L2 strength (smaller = stronger)")
    ] = 0.5,
    min_n: Annotated[
        int, typer.Option("--min-n", help="min labelled rows to train")
    ] = 20,
) -> None:
    """Fit the logistic-regression difficulty model on a labelled table."""
    from apply_ablate.difficulty import model as M

    rows = dataset.load_jsonl(table)
    trainable = M.trainable_rows(rows)
    _echo(f"{len(trainable)}/{len(rows)} rows trainable")
    try:
        fitted = M.train(trainable, C=c, min_n=min_n)
    except M.NotEnoughData as e:
        _echo(f"cannot train: {e}")
        raise typer.Exit(1) from e
    auc = (
        f"{fitted.cv_auc:.3f}"
        if fitted.cv_auc is not None
        else "n/a (too few per class)"
    )
    _echo(
        f"trained on {fitted.n_train} rows; PASS rate {fitted.pos_rate:.0%}; CV ROC-AUC {auc}"
    )
    _echo(f"features used: {len(fitted.feature_names)}")
    if fitted.dropped_features:
        _echo(f"  dropped (all-missing): {', '.join(fitted.dropped_features)}")
    M.save(fitted, out)
    _echo(f"wrote model -> {out}")


@app.command()
def score(
    challenges: Annotated[Path, typer.Argument(help="challenges.jsonl to score")],
    model_path: Annotated[Path, typer.Option("--model", help="fitted .joblib model")],
    out_jsonl: Annotated[
        Path | None, typer.Option("--out-jsonl", help="scored rows")
    ] = None,
) -> None:
    """Attach a difficulty score (= P(fail)) to each challenge."""
    from apply_ablate.difficulty import model as M

    fitted = M.load(model_path)
    feat_rows = dataset.extract_all(challenges)
    scores = fitted.score(feat_rows)
    scored = [
        {
            "challenge_id": r.get("challenge_id"),
            "task_id": r.get("task_id"),
            "file_path": r.get("file_path"),
            # carried through so pipeline/score_predictions.py can key its join on
            # (challenge_id, sample_mode) -- challenge_id alone does not disambiguate a
            # paired easy/hard sample (see pipeline/sample_paired.py)
            "sample_mode": r.get("sample_mode"),
            "difficulty": s,
        }
        for r, s in zip(feat_rows, scores, strict=True)
    ]
    scored.sort(key=lambda x: x["difficulty"], reverse=True)
    _echo(
        f"scored {len(scored)} challenge(s); difficulty range "
        f"[{min(scores):.3f}, {max(scores):.3f}]"
    )
    if out_jsonl:
        dataset.write_jsonl(scored, out_jsonl)
        _echo(f"wrote {len(scored)} rows -> {out_jsonl}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
