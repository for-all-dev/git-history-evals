"""Output module — writes eval challenges and commit records to JSONL format."""

from __future__ import annotations

import logging
from pathlib import Path

from scaffold.models import CommitRecord, EvalChallenge, MiningResult

logger = logging.getLogger(__name__)


def write_jsonl(challenges: list[EvalChallenge], output_path: str | Path) -> None:
    """Write challenges to a JSONL file (one JSON object per line)."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with open(output, "w") as f:
        for challenge in challenges:
            line = challenge.model_dump_json()
            f.write(line + "\n")

    logger.info("Wrote %d challenges to %s", len(challenges), output)


def write_mining_result(result: MiningResult, output_path: str | Path) -> None:
    """Write a full mining result to JSONL."""
    write_jsonl(result.challenges, output_path)


def read_jsonl(input_path: str | Path) -> list[EvalChallenge]:
    """Read challenges from a JSONL file."""
    challenges: list[EvalChallenge] = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                challenges.append(EvalChallenge.model_validate_json(line))
    return challenges


def write_commit_records(
    records: list[CommitRecord],
    output_path: str | Path,
) -> None:
    """Write CommitRecords to a JSONL file (one JSON object per line)."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        for rec in records:
            f.write(rec.model_dump_json() + "\n")
    logger.info("Wrote %d commit records to %s", len(records), output)


def read_commit_records(input_path: str | Path) -> list[CommitRecord]:
    """Read CommitRecords from a JSONL file."""
    records: list[CommitRecord] = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(CommitRecord.model_validate_json(line))
    return records


def print_stats(challenges: list[EvalChallenge]) -> None:
    """Print statistics about a set of challenges."""
    if not challenges:
        print("No challenges found.")
        return

    repos: dict[str, int] = {}
    assistants: dict[str, int] = {}
    total_holes = 0

    for c in challenges:
        repos[c.repo] = repos.get(c.repo, 0) + 1
        assistants[c.proof_assistant.value] = assistants.get(c.proof_assistant.value, 0) + 1
        total_holes += len(c.holes_filled)

    print(f"Total challenges: {len(challenges)}")
    print(f"Total holes filled: {total_holes}")
    print()
    print("By repository:")
    for repo, count in sorted(repos.items()):
        print(f"  {repo}: {count}")
    print()
    print("By proof assistant:")
    for pa, count in sorted(assistants.items()):
        print(f"  {pa}: {count}")