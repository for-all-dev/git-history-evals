"""CLI for the scaffold mining tool — profile-driven.

Every mining/enrichment command takes a ``--profile`` pointing at a RepoProfile
JSON (see scaffold/profile.py). The profile carries all repo-specific patterns;
the engine itself is repo-agnostic.
"""

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


def _load_compiled(profile_path: Path):
    """Load a RepoProfile JSON and return its CompiledProfile (regexes ready)."""
    from scaffold.profile import load_profile

    return load_profile(profile_path).compiled()


_PROFILE_OPT = typer.Option(
    ..., "--profile", "-p", help="Path to a RepoProfile JSON (scaffold/profile.py)."
)


@app.command()
def mine(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    profile: Path = _PROFILE_OPT,
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

    from scaffold.analyzers import ProfileAnalyzer
    from scaffold.git_walker import mine_repo
    from scaffold.output import write_mining_result

    analyzer = ProfileAnalyzer(_load_compiled(profile))
    result = mine_repo(
        repo_path,
        repo_path.name,
        analyzer,
        max_commits=limit,
        start_ref=start_ref,
        dry_run=dry_run,
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
    artifacts_dir: Path = typer.Option(
        "./artifacts",
        "--artifacts-dir",
        help="Where per-repo profiles live: <artifacts>/<repo>-eval/profile.json",
    ),
    limit: int | None = typer.Option(None, "--limit", "-n"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Mine every repo in the data directory that has a profile."""
    _setup_logging(verbose)

    from scaffold.analyzers import ProfileAnalyzer
    from scaffold.git_walker import mine_repo
    from scaffold.output import write_mining_result

    if not data_dir.exists():
        typer.echo(f"Data directory not found: {data_dir}", err=True)
        raise typer.Exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    for entry in sorted(data_dir.iterdir()):
        if not entry.is_dir() or not (entry / ".git").exists():
            continue

        profile_path = artifacts_dir / f"{entry.name}-eval" / "profile.json"
        if not profile_path.exists():
            typer.echo(f"--- Skipping {entry.name}: no profile at {profile_path} ---")
            continue

        typer.echo(f"\n--- Mining {entry.name} ---")
        analyzer = ProfileAnalyzer(_load_compiled(profile_path))
        result = mine_repo(entry, entry.name, analyzer, max_commits=limit)
        out_path = output_dir / f"{entry.name}.jsonl"
        write_mining_result(result, out_path)
        typer.echo(f"  {result.total_challenges} challenges -> {out_path}")


@app.command()
def dump_commits(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    profile: Path = _PROFILE_OPT,
    output_dir: Path = typer.Option("./artifacts", "--output-dir", "-o"),
    limit: int | None = typer.Option(None, "--limit", "-n", help="Max commits to dump"),
    start_ref: str = typer.Option("HEAD", "--ref", help="Git ref to start from"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Dump all commit records to two JSONL datasets.

    Produces:
      <output_dir>/<repo_name>-commits-all.jsonl   — every commit
      <output_dir>/<repo_name>-commits-coq.jsonl   — only commits touching proof files
    """
    _setup_logging(verbose)

    from scaffold.git_walker import dump_commits as _dump
    from scaffold.output import write_commit_records

    compiled = _load_compiled(profile)
    name = repo_path.name
    records = _dump(repo_path, compiled, start_ref=start_ref, max_commits=limit)

    output_dir.mkdir(parents=True, exist_ok=True)

    all_path = output_dir / f"{name}-commits-all.jsonl"
    coq_path = output_dir / f"{name}-commits-coq.jsonl"

    coq_records = [r for r in records if r.touches_proof_files]

    write_commit_records(records, all_path)
    write_commit_records(coq_records, coq_path)

    typer.echo(f"All commits  : {len(records):>6} records -> {all_path}")
    typer.echo(f"Coq commits  : {len(coq_records):>6} records -> {coq_path}")


@app.command()
def enrich_commits(
    input_path: Path = typer.Argument(..., help="Path to a commits JSONL file"),
    profile: Path = _PROFILE_OPT,
    output_path: Path = typer.Option(
        None, "--output", "-o", help="Output path (default: overwrites input)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Enrich a commit JSONL with commit_class and keywords (message heuristics)."""
    _setup_logging(verbose)

    from collections import Counter

    from scaffold.output import read_commit_records, write_commit_records
    from scaffold.pattern_detector import enrich_record

    compiled = _load_compiled(profile)
    records = read_commit_records(input_path)
    enriched = [enrich_record(r, compiled) for r in records]

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
    profile: Path = _PROFILE_OPT,
    output_path: Path = typer.Option(
        None, "--output", "-o", help="Output path (default: overwrites input)"
    ),
    only_proof: bool = typer.Option(
        True,
        "--only-proof/--all",
        help="Only re-classify commits that touch proof files (default: True)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Second-pass diff-based enrichment: read actual proof-file diffs to classify."""
    _setup_logging(verbose)

    import concurrent.futures
    from collections import Counter

    from scaffold.models import CommitRecord
    from scaffold.output import read_commit_records, write_commit_records
    from scaffold.pattern_detector import enrich_record_with_diff

    compiled = _load_compiled(profile)
    records = read_commit_records(input_path)
    to_enrich = [r for r in records if r.proof_files_changed] if only_proof else records
    enrich_hashes = {r.hash for r in to_enrich}
    keep = [r for r in records if r.hash not in enrich_hashes] if only_proof else []

    typer.echo(f"Diff-enriching {len(to_enrich)} records (repo: {repo_path}) ...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {
            pool.submit(enrich_record_with_diff, r, repo_path, compiled): i
            for i, r in enumerate(to_enrich)
        }
        done = 0
        results: dict[int, CommitRecord] = {}
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
        None,
        "--output-dir",
        "-o",
        help="Directory for tactic subdatasets (default: same dir as input)",
    ),
) -> None:
    """Split diff-enriched proof_add records into per-tactic subdataset files."""
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
def group_tactics(
    input_path: Path = typer.Argument(..., help="Diff-enriched commits JSONL"),
    profile: Path = _PROFILE_OPT,
    output_path: Path = typer.Option(
        None, "--output", "-o", help="Output path (default: overwrites input)"
    ),
    output_dir: Path = typer.Option(
        None,
        "--output-dir",
        "-d",
        help="Directory for per-group subdataset files (default: same dir as input)",
    ),
) -> None:
    """Assign behavioural tactic groups to each record and write per-group subdatasets."""
    from collections import Counter, defaultdict

    from scaffold.output import read_commit_records, write_commit_records
    from scaffold.pattern_detector import assign_tactic_groups

    compiled = _load_compiled(profile)
    records = read_commit_records(input_path)

    enriched = [
        r.model_copy(
            update={"tactic_group_tags": assign_tactic_groups(r.tactic_tags, compiled)}
        )
        for r in records
    ]

    dest = output_path or input_path
    write_commit_records(enriched, dest)
    typer.echo(f"Wrote {len(enriched)} records to {dest}")

    counts: Counter[str] = Counter()
    for r in enriched:
        for g in r.tactic_group_tags:
            counts[g] += 1
    typer.echo(
        "\nTactic group distribution (proof_add commits may appear in multiple groups):"
    )
    for grp, n in sorted(counts.items(), key=lambda x: -x[1]):
        typer.echo(f"  {grp:<28} {n:>5}")

    out_dir = output_dir or input_path.parent / "group-subdatasets"
    out_dir.mkdir(parents=True, exist_ok=True)

    buckets: dict[str, list] = defaultdict(list)
    for r in enriched:
        for g in r.tactic_group_tags:
            buckets[g].append(r)

    typer.echo(f"\nPer-group subdatasets -> {out_dir}/")
    for grp, recs in sorted(buckets.items(), key=lambda x: -len(x[1])):
        out_path = out_dir / f"group-{grp}.jsonl"
        write_commit_records(recs, out_path)
        typer.echo(f"  {grp:<28} {len(recs):>5} records -> {out_path.name}")


@app.command()
def profile(
    repo_path: Path = typer.Argument(
        ..., help="Path to the proof engineering repo to calibrate"
    ),
    model: str = typer.Option(
        "anthropic:claude-sonnet-4-6", "--model", "-m", help="pydantic-ai model string"
    ),
    tag: str = typer.Option(
        "agent",
        "--tag",
        "-t",
        help="Human-readable version tag (e.g. 'agentic_1'); dir is <tag>-<short_hash>",
    ),
    promote: bool = typer.Option(
        False,
        "--promote",
        help="Symlink <repo>-eval/profile.json to this version's profile (the blessed profile mine-all uses)",
    ),
    artifacts_dir: Path = typer.Option(
        None,
        "--artifacts-dir",
        help="Artifacts root (default: <monorepo>/artifacts)",
    ),
    request_limit: int = typer.Option(
        80, "--request-limit", help="Max agent request round-trips (UsageLimits)"
    ),
    test_commits: int = typer.Option(
        1500,
        "--test-commits",
        help="Commits sampled per test_profile call during calibration",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Synthesise a RepoProfile by exploring a repo with the calibration agent.

    Tier-1 of the agentic miner: a CodeMode pydantic-ai agent detects the repo's
    proof assistant, file globs, hole markers, declaration patterns, commit
    conventions, and tactic vocabulary on the fly, validating against the real
    engine, then emits a profile the deterministic miner can consume.
    """
    _setup_logging(verbose)

    from scaffold.profiler import build_profile

    result = build_profile(
        repo_path,
        model=model,
        tag=tag,
        promote=promote,
        artifacts_root=artifacts_dir,
        request_limit=request_limit,
        test_commits=test_commits,
    )

    typer.echo(f"\nDataset version : {result.version}")
    typer.echo(f"  path            : {result.dataset_path}")
    typer.echo(f"  challenges      : {result.n_challenges}")
    typer.echo(f"  manifest_hash   : {result.manifest_hash[:12]}")
    typer.echo(f"  promoted        : {result.promoted}")
    typer.echo(f"  proof_assistant : {result.profile.proof_assistant}")
    typer.echo(f"  hole_markers    : {[h.kind for h in result.profile.hole_markers]}")
    typer.echo(f"  tactic vocab    : {len(result.profile.tactic_vocabulary)} tactics")


@app.command()
def materialize(
    repo_path: Path = typer.Argument(..., help="Path to the proof engineering repo"),
    profile: Path = _PROFILE_OPT,
    tag: str = typer.Option(
        "v1-handcrafted", "--tag", "-t", help="Version tag; dir is <tag>-<short_hash>"
    ),
    kind: str = typer.Option(
        "handcrafted",
        "--kind",
        help="Miner kind for the manifest: 'handcrafted' or 'agent'",
    ),
    promote: bool = typer.Option(
        False,
        "--promote",
        help="Bless this dataset: symlink <repo>-eval/profile.json -> its profile",
    ),
    artifacts_dir: Path = typer.Option(
        None, "--artifacts-dir", help="Artifacts root (default: <monorepo>/artifacts)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Bundle an existing RepoProfile into a manifest-schema dataset version dir (no agent)."""
    _setup_logging(verbose)
    from scaffold.dataset import mine_and_materialize, promote_profile
    from scaffold.profile import load_profile

    prof = load_profile(profile)
    dv = mine_and_materialize(
        profile=prof,
        repo_path=repo_path,
        tag=tag,
        miner_kind=kind,
        artifacts_root=artifacts_dir,
    )
    typer.echo(f"Materialized {dv.version} ({dv.n_challenges} challenges) -> {dv.path}")
    typer.echo(f"  manifest_hash: {dv.manifest_hash[:12]}")
    if promote:
        blessed = promote_profile(dv)
        typer.echo(f"  promoted (symlink) -> {blessed}")


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
