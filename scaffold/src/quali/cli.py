"""CLI for the qualitative proof trajectory study."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from .study import load_env, run_study

# Find repo root by walking up from this file to the dir containing `artifacts/`
_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parent.parent.parent  # quali/ -> src/ -> scaffold/ -> repo root
_ARTIFACTS = _REPO_ROOT / "artifacts"

app = typer.Typer(help="Qualitative study of proof evolution trajectories.")


@app.command()
def analyze(
    lifecycle: Path = typer.Option(
        None,
        "--lifecycle",
        "-l",
        help="Path to lifecycle JSONL file",
    ),
    grouped: Path = typer.Option(
        None,
        "--grouped",
        "-g",
        help="Path to grouped commits JSONL file",
    ),
    output: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output JSONL path",
    ),
    model: str = typer.Option(
        "anthropic:claude-sonnet-4-20250514",
        "--model",
        "-m",
        help="pydantic-ai model string",
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Max trajectories to analyze"),
    min_commits: int = typer.Option(
        3, "--min-commits", help="Min commits per trajectory"
    ),
    min_days: int = typer.Option(1, "--min-days", help="Min days_to_prove"),
) -> None:
    """Analyze human proof engineering trajectories qualitatively."""
    load_env()

    lifecycle = (lifecycle or _ARTIFACTS / "fiat-crypto-lifecycle.jsonl").resolve()
    grouped = (grouped or _ARTIFACTS / "fiat-crypto-commits-coq-grouped.jsonl").resolve()
    output = (output or _ARTIFACTS / "fiat-crypto-quali.jsonl").resolve()

    if output.exists():
        output.unlink()

    asyncio.run(
        run_study(
            lifecycle_path=lifecycle,
            grouped_path=grouped,
            output_path=output,
            model=model,
            limit=limit,
            min_commits=min_commits,
            min_days=min_days,
        )
    )


def main() -> None:
    app()
