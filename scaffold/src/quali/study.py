"""Core analysis logic: load artifacts, build prompts, run pydantic-ai agent."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic_ai import Agent
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from .models import TrajectoryAnalysis

console = Console()

SYSTEM_PROMPT = """\
You are a qualitative researcher studying how human proof engineers develop \
formal proofs over time. You are analyzing commit histories from the fiat-crypto \
repository, which implements verified cryptographic primitives in Coq.

Your task: analyze the evolution of a single theorem/lemma across its commit \
history and produce structured observations plus an interpretive narrative.

Available observation codes:
- strategy_shift: the proof approach fundamentally changed (e.g. rewrite-heavy to automation)
- collaboration_handoff: a different author took over the proof
- backtrack: work was undone or a dead end was abandoned
- incremental_progress: steady forward progress on the proof
- sorry_introduced: a proof hole (Admitted/admit) was first introduced
- sorry_resolved: a proof hole was filled / closed
- tactic_style_change: shift in proof style (tactic mode vs term mode vs ssreflect)
- proof_compression: making the proof shorter/cleaner without changing the result
- specification_change: the theorem statement itself was modified during development
- blocked_period: a long gap between commits suggests the prover was stuck
- exploratory_phase: rapid small commits suggest exploration/experimentation
- breakthrough: a single commit made dramatic progress

Focus on what makes this trajectory distinctly *human*: iteration, false starts, \
collaboration patterns, the role of insight vs. grinding, how proof strategies \
evolve. These observations will later be contrasted with AI agent proof trajectories.

When noting a blocked_period, consider the gap relative to the surrounding commit \
frequency -- a 2-week gap in a daily-commit project is more significant than in a \
monthly-commit project.

Ground every observation in specific commit data. Do not speculate beyond what the \
evidence supports.\
"""


def _find_dotenv() -> Path | None:
    """Walk up from CWD looking for a .env file."""
    cur = Path.cwd()
    while True:
        candidate = cur / ".env"
        if candidate.is_file():
            return candidate
        parent = cur.parent
        if parent == cur:
            return None
        cur = parent


def load_env(path: Path | None = None) -> None:
    """Load a .env file. If no path given, searches up from CWD."""
    if path is None:
        path = _find_dotenv()
    if path is None or not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def load_commit_index(grouped_path: Path) -> dict[str, dict]:
    """Load grouped commits into a hash -> record dict for enrichment."""
    index: dict[str, dict] = {}
    with open(grouped_path) as f:
        for line in f:
            rec = json.loads(line)
            index[rec["hash"]] = rec
    return index


def load_lifecycles(lifecycle_path: Path) -> list[dict]:
    records: list[dict] = []
    with open(lifecycle_path) as f:
        for line in f:
            records.append(json.loads(line))
    return records


def enrich_timeline(lifecycle: dict, commit_index: dict[str, dict]) -> list[dict]:
    """Join timeline commits with full commit records for author/body info."""
    enriched = []
    for entry in lifecycle.get("commit_timeline", []):
        full = commit_index.get(entry["hash"], {})
        enriched.append(
            {
                **entry,
                "author": full.get("author", "unknown"),
                "message_body": full.get("message_body", ""),
                "proof_style": full.get("proof_style", []),
                "insertions": full.get("insertions", 0),
                "deletions": full.get("deletions", 0),
            }
        )
    return enriched


def format_trajectory_prompt(lifecycle: dict, enriched_timeline: list[dict]) -> str:
    """Build the user prompt for a single theorem trajectory."""
    lines = [
        f"## Declaration: `{lifecycle['declaration']}`",
        f"**File:** `{lifecycle['file']}`",
        f"**Hole kind:** {lifecycle['hole_kind']}",
        f"**First hole:** {lifecycle['first_hole_date']}",
        f"**Proof completed:** {lifecycle['proof_complete_date']}",
        f"**Days to prove:** {lifecycle['days_to_prove']}",
        f"**Commits touching this proof:** {lifecycle['n_commits_with_hole']}",
        f"**Top tactics used:** {', '.join(lifecycle.get('top_tactics', [])[:15])}",
        f"**Tactic groups:** {', '.join(lifecycle.get('tactic_groups_used', []))}",
        "",
        "### Commit Timeline",
        "",
    ]

    for i, entry in enumerate(enriched_timeline):
        lines.append(f"**Commit {i + 1}** ({entry['date'][:10]})")
        lines.append(f"- Hash: `{entry['hash'][:12]}`")
        lines.append(f"- Author: {entry['author']}")
        lines.append(f"- Subject: {entry['subject']}")
        if entry.get("message_body"):
            body = entry["message_body"][:500]
            lines.append(f"- Body: {body}")
        lines.append(f"- Class: {entry['commit_class']}")
        lines.append(f"- Net proof lines: {entry['net_proof_lines']:+d}")
        if entry.get("tactic_tags"):
            lines.append(f"- Tactics: {', '.join(entry['tactic_tags'][:20])}")
        if entry.get("proof_style"):
            lines.append(f"- Style: {', '.join(entry['proof_style'])}")
        lines.append("")

    lines.append(
        "Analyze this proof's evolution. What does the trajectory reveal about "
        "the human proof engineering process?"
    )
    return "\n".join(lines)


def create_agent(
    model: str = "anthropic:claude-sonnet-4-20250514",
) -> Agent[None, TrajectoryAnalysis]:
    return Agent(
        model,
        output_type=TrajectoryAnalysis,
        system_prompt=SYSTEM_PROMPT,
    )


async def analyze_trajectory(
    agent: Agent[None, TrajectoryAnalysis],
    lifecycle: dict,
    commit_index: dict[str, dict],
) -> TrajectoryAnalysis:
    enriched = enrich_timeline(lifecycle, commit_index)
    prompt = format_trajectory_prompt(lifecycle, enriched)
    result = await agent.run(prompt)
    return result.output


async def run_study(
    lifecycle_path: Path,
    grouped_path: Path,
    output_path: Path,
    *,
    model: str = "anthropic:claude-sonnet-4-6",
    limit: int = 10,
    min_commits: int = 3,
    min_days: int = 1,
) -> list[dict]:
    """Run the qualitative study on proof trajectories."""
    console.print(f"Loading commit index from [dim]{grouped_path}[/dim]...")
    commit_index = load_commit_index(grouped_path)
    console.print(f"Loading lifecycles from [dim]{lifecycle_path}[/dim]...")
    lifecycles = load_lifecycles(lifecycle_path)

    # Filter to trajectories with enough signal
    candidates = [
        lc
        for lc in lifecycles
        if lc["n_commits_with_hole"] >= min_commits and lc["days_to_prove"] >= min_days
    ]
    # Richer trajectories first
    candidates.sort(key=lambda lc: lc["n_commits_with_hole"], reverse=True)
    selected = candidates[:limit]

    console.print(
        f"Selected [bold]{len(selected)}[/bold] trajectories from {len(candidates)} candidates "
        f"(filtered from {len(lifecycles)} total)"
    )

    agent = create_agent(model)
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing trajectories", total=len(selected))

        for lifecycle in selected:
            decl = lifecycle["declaration"]
            n = lifecycle["n_commits_with_hole"]
            d = lifecycle["days_to_prove"]
            progress.update(task, description=f"[cyan]{decl}[/cyan] ({n} commits, {d}d)")

            try:
                analysis = await analyze_trajectory(agent, lifecycle, commit_index)
                record = {
                    "source": {
                        "declaration": lifecycle["declaration"],
                        "file": lifecycle["file"],
                        "hole_kind": lifecycle["hole_kind"],
                        "first_hole_date": lifecycle["first_hole_date"],
                        "proof_complete_date": lifecycle["proof_complete_date"],
                        "days_to_prove": lifecycle["days_to_prove"],
                        "n_commits": lifecycle["n_commits_with_hole"],
                        "top_tactics": lifecycle.get("top_tactics", []),
                    },
                    "analysis": analysis.model_dump(),
                }
                results.append(record)
                with open(output_path, "a") as f:
                    f.write(json.dumps(record) + "\n")
                progress.console.print(
                    f"  [green]{len(analysis.observations)} obs[/green], "
                    f"complexity={analysis.complexity}, "
                    f"strategy=[dim]{analysis.dominant_strategy}[/dim]"
                )
            except Exception as e:
                progress.console.print(f"  [red]Error: {e}[/red]")

            progress.advance(task)

    console.print(f"\nWrote [bold]{len(results)}[/bold] analyses to [dim]{output_path}[/dim]")
    return results
