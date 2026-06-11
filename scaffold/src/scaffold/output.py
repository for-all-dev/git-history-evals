"""Output module — writes eval challenges and commit records to JSONL format."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from scaffold.models import CommitRecord, EvalChallenge, MiningResult

logger = logging.getLogger(__name__)

# Heavy EvalChallenge fields that curation/calibration never read (the prompts
# are built from diff + metadata only). Blanking them on load keeps a
# 25k-challenge / 1GB dataset at a flat, small memory footprint.
SLIM_BLANK_FIELDS = ("challenge_file_content", "solution_file_content")


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


def read_jsonl_slim(input_path: str | Path) -> list[EvalChallenge]:
    """Read challenges with the heavy file-content fields blanked.

    Curation and calibration only consume diff + metadata; loading the full
    challenge/solution file contents for a large dataset (e.g. fiat-crypto's
    1GB / 25k challenges) makes the process a memory-pressure kill target.
    Use :func:`write_curated_stream` to produce full-fidelity output without
    ever holding the heavy fields in memory.
    """
    challenges: list[EvalChallenge] = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            for field in SLIM_BLANK_FIELDS:
                record[field] = ""
            challenges.append(EvalChallenge.model_validate(record))
    return challenges


def write_curated_stream(
    input_path: str | Path,
    output_path: str | Path,
    verdicts: dict[str, tuple[str, str, str]],
) -> int:
    """Stream-write curation survivors with their annotations; return count.

    *verdicts* maps task_id -> (verdict, model, rationale) for challenges to
    KEEP (accept/borderline). Each kept line is re-read from *input_path*
    (which has the full file contents that slim loading dropped), annotated,
    and re-serialized — one challenge in memory at a time.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    with open(input_path) as fin, open(output, "w") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            entry = verdicts.get(record.get("task_id", ""))
            if entry is None:
                continue
            verdict, model, rationale = entry
            record["curation_verdict"] = verdict
            record["curation_model"] = model
            record["curation_rationale"] = rationale
            fout.write(EvalChallenge.model_validate(record).model_dump_json() + "\n")
            n_written += 1
    logger.info("Wrote %d curated challenges to %s", n_written, output)
    return n_written


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
        assistants[c.proof_assistant] = assistants.get(c.proof_assistant, 0) + 1
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
