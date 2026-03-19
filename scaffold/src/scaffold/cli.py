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
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Log candidates without full extraction"
    ),
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

    from scaffold.analyzers import get_analyzer
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
def dump_commits(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    output_dir: Path = typer.Option("./artifacts", "--output-dir", "-o"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max commits to dump"),
    start_ref: str = typer.Option("HEAD", "--ref", help="Git ref to start from"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Dump all commit records to two JSONL datasets.

    Produces:
      <output_dir>/<repo_name>-commits-all.jsonl   — every commit
      <output_dir>/<repo_name>-commits-coq.jsonl   — only commits touching .v files
    """
    _setup_logging(verbose)

    from scaffold.git_walker import dump_commits as _dump
    from scaffold.output import write_commit_records
    from scaffold.pattern_detector import analyze_repo

    metadata = analyze_repo(repo_path)
    records = _dump(repo_path, start_ref=start_ref, max_commits=limit)

    output_dir.mkdir(parents=True, exist_ok=True)

    all_path = output_dir / f"{metadata.name}-commits-all.jsonl"
    coq_path = output_dir / f"{metadata.name}-commits-coq.jsonl"

    coq_records = [r for r in records if r.touches_proof_files]

    write_commit_records(records, all_path)
    write_commit_records(coq_records, coq_path)

    typer.echo(f"All commits  : {len(records):>6} records -> {all_path}")
    typer.echo(f"Coq commits  : {len(coq_records):>6} records -> {coq_path}")


@app.command()
def enrich_commits(
    input_path: Path = typer.Argument(..., help="Path to a commits JSONL file"),
    output_path: Path = typer.Option(
        None,
        "--output",
        "-o",
        help="Output path (default: overwrites input)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Enrich a commit JSONL with commit_class and keywords.

    Reads an existing commits JSONL, runs the heuristic classifier over every
    record, and writes the enriched records back out. Fast — pure Python, no
    git calls required.

    Class distribution is printed after writing so you can assess quality.
    """
    _setup_logging(verbose)

    from collections import Counter

    from scaffold.output import read_commit_records, write_commit_records
    from scaffold.pattern_detector import enrich_record

    records = read_commit_records(input_path)
    enriched = [enrich_record(r) for r in records]

    dest = output_path or input_path
    write_commit_records(enriched, dest)

    counts: Counter[str] = Counter(r.commit_class.value for r in enriched)
    typer.echo(f"\nWrote {len(enriched)} records to {dest}")
    typer.echo("\nClass distribution:")
    for cls, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(enriched)
        typer.echo(f"  {cls:<18} {count:>6}  ({pct:.1f}%)")


@app.command()
def diff_enrich(
    input_path: Path = typer.Argument(..., help="Labeled commits JSONL to enrich"),
    repo_path: Path = typer.Argument(..., help="Path to the source git repo"),
    output_path: Path = typer.Option(
        None, "--output", "-o", help="Output path (default: overwrites input)"
    ),
    only_proof: bool = typer.Option(
        True,
        "--only-proof/--all",
        help="Only re-classify commits that touch .v files (default: True)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Second-pass diff-based enrichment: read actual .v diffs to classify commits.

    For every commit that touches .v files this reads the real git diff and:
      - sets diff_sorry_removed, diff_net_proof_lines
      - populates tactic_tags and proof_style from added lines
      - upgrades commit_class to proof_complete / proof_optimise / proof_add
        based on what the diff actually shows

    Class distribution is printed after writing.
    """
    _setup_logging(verbose)

    import concurrent.futures
    from collections import Counter

    from scaffold.output import read_commit_records, write_commit_records
    from scaffold.pattern_detector import enrich_record_with_diff

    records = read_commit_records(input_path)
    to_enrich = [r for r in records if r.coq_files_changed] if only_proof else records
    enrich_hashes = {r.hash for r in to_enrich}
    keep = [r for r in records if r.hash not in enrich_hashes] if only_proof else []

    typer.echo(f"Diff-enriching {len(to_enrich)} records (repo: {repo_path}) ...")

    enriched: list = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {
            pool.submit(enrich_record_with_diff, r, repo_path): i
            for i, r in enumerate(to_enrich)
        }
        done = 0
        results: dict[int, object] = {}
        for fut in concurrent.futures.as_completed(futs):
            idx = futs[fut]
            results[idx] = fut.result()
            done += 1
            if done % 500 == 0:
                typer.echo(f"  {done}/{len(to_enrich)} ...")

    enriched = [results[i] for i in range(len(to_enrich))]
    all_records = enriched + keep

    dest = output_path or input_path
    write_commit_records(all_records, dest)

    counts: Counter[str] = Counter(r.commit_class.value for r in enriched)
    typer.echo(f"\nWrote {len(all_records)} records to {dest}")
    typer.echo("\nClass distribution (diff-enriched records):")
    for cls, count in sorted(counts.items(), key=lambda x: -x[1]):
        pct = 100 * count / len(enriched)
        typer.echo(f"  {cls:<18} {count:>6}  ({pct:.1f}%)")


@app.command()
def stratify_tactics(
    input_path: Path = typer.Argument(..., help="Diff-enriched commits JSONL"),
    output_dir: Path = typer.Option(
        None, "--output-dir", "-o",
        help="Directory for tactic subdatasets (default: same dir as input)"
    ),
) -> None:
    """Split diff-enriched proof_add records into per-tactic subdataset files.

    Reads a diff-enriched JSONL and writes one file per tactic tag, e.g.:
      <output_dir>/tactic-rewrite.jsonl
      <output_dir>/tactic-induction.jsonl
      ...

    A record appears in multiple files if it uses multiple tactics.
    Also writes tactic-term_mode.jsonl and tactic-ssreflect.jsonl from proof_style.
    """
    from collections import defaultdict

    from scaffold.models import CommitClass
    from scaffold.output import read_commit_records, write_commit_records

    records = read_commit_records(input_path)
    proof_add = [r for r in records if r.commit_class == CommitClass.proof_add]

    out_dir = output_dir or input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    buckets: dict[str, list] = defaultdict(list)
    for r in proof_add:
        for tag in r.tactic_tags:
            buckets[tag].append(r)
        for style in r.proof_style:
            if style not in ("tactic_mode", "unknown"):
                buckets[f"style_{style}"].append(r)

    if not buckets:
        typer.echo("No tactic_tags found — run diff-enrich first.")
        raise typer.Exit(1)

    for tag, recs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        out_path = out_dir / f"tactic-{tag}.jsonl"
        write_commit_records(recs, out_path)
        typer.echo(f"  {tag:<25} {len(recs):>5} records -> {out_path.name}")


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