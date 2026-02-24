"""CLI for the scaffold mining tool."""

from __future__ import annotations

import logging
from pathlib import Path

import typer

app = typer.Typer(
    name="scaffold",
    help="Mine proof engineering git histories for eval challenges.",
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )


@app.command()
def analyze(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Analyze a repo: detect proof assistant, build system, and patterns."""
    _setup_logging(verbose)

    from scaffold.pattern_detector import analyze_repo

    metadata = analyze_repo(repo_path)

    typer.echo(f"Repository: {metadata.name}")
    typer.echo(f"Proof assistant: {metadata.proof_assistant.value}")
    typer.echo(f"File extensions: {metadata.file_extensions}")
    typer.echo(f"URL: {metadata.url or '(not detected)'}")
    typer.echo(f"Exclude paths: {metadata.exclude_paths or '(none)'}")

    if metadata.discovered_patterns:
        typer.echo("Discovered patterns:")
        for key, val in metadata.discovered_patterns.items():
            typer.echo(f"  {key}: {val}")


@app.command()
def mine(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    output: Path = typer.Option("output.jsonl", "--output", "-o"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max commits to scan"),
    start_ref: str = typer.Option("HEAD", "--ref", help="Git ref to start from"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Log candidates without full extraction"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Mine a single repo for eval challenges."""
    _setup_logging(verbose)

    from scaffold.analyzers import get_analyzer
    from scaffold.git_walker import mine_repo
    from scaffold.output import write_mining_result
    from scaffold.pattern_detector import analyze_repo

    metadata = analyze_repo(repo_path)
    analyzer = get_analyzer(metadata.proof_assistant)

    result = mine_repo(
        metadata, analyzer, max_commits=limit, start_ref=start_ref, dry_run=dry_run
    )

    if not dry_run:
        write_mining_result(result, output)
        typer.echo(f"Wrote {result.total_challenges} challenges to {output}")
    else:
        typer.echo(f"[dry-run] Scanned {result.total_commits_scanned} commits")


@app.command()
def mine_all(
    data_dir: Path = typer.Option("./data", "--data-dir", "-d"),
    output_dir: Path = typer.Option("./artifacts", "--output-dir", "-o"),
    limit: int | None = typer.Option(None, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Mine all repos in the data directory."""
    _setup_logging(verbose)

    from scaffold.analyzers import detect_proof_assistant, get_analyzer
    from scaffold.git_walker import mine_repo
    from scaffold.output import write_mining_result
    from scaffold.pattern_detector import analyze_repo

    if not data_dir.exists():
        typer.echo(f"Data directory not found: {data_dir}", err=True)
        raise typer.Exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    for entry in sorted(data_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not (entry / ".git").exists():
            continue

        typer.echo(f"\n--- Mining {entry.name} ---")
        metadata = analyze_repo(entry)
        analyzer = get_analyzer(metadata.proof_assistant)

        result = mine_repo(metadata, analyzer, max_commits=limit)
        out_path = output_dir / f"{entry.name}.jsonl"
        write_mining_result(result, out_path)
        typer.echo(f"  {result.total_challenges} challenges -> {out_path}")


@app.command()
def stats(
    jsonl_path: Path = typer.Argument(..., help="Path to a .jsonl challenges file"),
) -> None:
    """Print statistics about mined challenges."""
    from scaffold.output import print_stats, read_jsonl

    challenges = read_jsonl(jsonl_path)
    print_stats(challenges)


def main() -> None:
    app()
