"""Git history walker — mines proof engineering repos for eval challenges.

Uses raw git subprocess calls for performance on large repos (no GitPython).
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from scaffold.analyzers.base import ProofAnalyzer
from scaffold.models import (
    EvalChallenge,
    MiningResult,
    RepoMetadata,
)

logger = logging.getLogger(__name__)

# git log format: hash, parent hash, author, date (ISO), subject
_LOG_FORMAT = "%H%x00%P%x00%an%x00%aI%x00%s"
_LOG_SEP = "\x00"


@dataclass
class RawCommit:
    hash: str
    parent_hash: str
    author: str
    date: str
    message: str


def _run_git(
    repo_path: str | Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a git command in the given repo."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        capture_output=True,
        text=True,
        check=check,
    )


def iter_commits(
    repo_path: str | Path,
    start_ref: str = "HEAD",
    max_commits: int | None = None,
) -> list[RawCommit]:
    """List commits from start_ref backwards."""
    cmd = ["log", f"--format={_LOG_FORMAT}", start_ref]
    if max_commits is not None:
        cmd.append(f"-n{max_commits}")

    result = _run_git(repo_path, *cmd)
    commits: list[RawCommit] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(_LOG_SEP)
        if len(parts) < 5:
            continue
        parent = parts[1].split()[0] if parts[1] else ""
        commits.append(
            RawCommit(
                hash=parts[0],
                parent_hash=parent,
                author=parts[2],
                date=parts[3],
                message=parts[4],
            )
        )
    return commits


def get_file_at_commit(
    repo_path: str | Path, commit_hash: str, file_path: str
) -> str | None:
    """Get file content at a specific commit without checkout."""
    result = _run_git(
        repo_path, "show", f"{commit_hash}:{file_path}", check=False
    )
    if result.returncode != 0:
        return None
    return result.stdout


def get_diff_text(
    repo_path: str | Path, parent_hash: str, child_hash: str, file_path: str
) -> str:
    """Get unified diff for a single file between two commits."""
    result = _run_git(
        repo_path,
        "diff",
        parent_hash,
        child_hash,
        "--",
        file_path,
        check=False,
    )
    return result.stdout


def get_modified_files(
    repo_path: str | Path,
    parent_hash: str,
    child_hash: str,
    analyzer: ProofAnalyzer,
) -> list[str]:
    """Get list of proof files modified between parent and child commits."""
    result = _run_git(
        repo_path,
        "diff",
        "--name-only",
        "--diff-filter=M",
        parent_hash,
        child_hash,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [
        f
        for f in result.stdout.strip().splitlines()
        if analyzer.matches_file(f)
    ]


def _make_task_id(repo_name: str, commit_hash: str, file_path: str) -> str:
    """Create a deterministic task ID."""
    raw = f"{repo_name}_{commit_hash[:12]}_{file_path}"
    suffix = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{repo_name}_{commit_hash[:8]}_{suffix}"


def mine_commit(
    repo_path: str | Path,
    commit: RawCommit,
    analyzer: ProofAnalyzer,
    repo_name: str,
) -> list[EvalChallenge]:
    """Mine a single commit for eval challenges."""
    if not commit.parent_hash:
        return []

    modified = get_modified_files(
        repo_path, commit.parent_hash, commit.hash, analyzer
    )
    challenges: list[EvalChallenge] = []

    for fpath in modified:
        parent_content = get_file_at_commit(repo_path, commit.parent_hash, fpath)
        child_content = get_file_at_commit(repo_path, commit.hash, fpath)

        if parent_content is None or child_content is None:
            continue

        filled = analyzer.find_filled_holes(parent_content, child_content, fpath)
        if not filled:
            continue

        diff = get_diff_text(repo_path, commit.parent_hash, commit.hash, fpath)

        hole_descriptions = ", ".join(
            f"{h.kind.value} in {h.enclosing_decl or 'unknown'}" for h in filled
        )
        instructions = (
            f"Fill in the proof(s) marked with placeholder tactics "
            f"({hole_descriptions}) in {fpath}."
        )

        challenges.append(
            EvalChallenge(
                task_id=_make_task_id(repo_name, commit.hash, fpath),
                repo=repo_name,
                proof_assistant=analyzer.proof_assistant,
                commit_hash=commit.hash,
                parent_hash=commit.parent_hash,
                commit_message=commit.message,
                file_path=fpath,
                challenge_file_content=parent_content,
                solution_file_content=child_content,
                holes_filled=filled,
                diff=diff,
                instructions=instructions,
            )
        )

    return challenges


def mine_repo(
    metadata: RepoMetadata,
    analyzer: ProofAnalyzer,
    max_commits: int | None = None,
    start_ref: str = "HEAD",
    dry_run: bool = False,
) -> MiningResult:
    """Main pipeline: walk commits, find proof-file diffs, detect filled holes.

    Args:
        metadata: Repository metadata.
        analyzer: Language-specific proof analyzer.
        max_commits: Limit number of commits to scan.
        start_ref: Git ref to start walking from.
        dry_run: If True, log candidates but don't build full challenges.
    """
    repo_path = metadata.local_path
    commits = iter_commits(repo_path, start_ref, max_commits)
    logger.info("Scanning %d commits in %s", len(commits), metadata.name)

    all_challenges: list[EvalChallenge] = []

    for i, commit in enumerate(commits):
        if i % 100 == 0:
            logger.info("Progress: %d/%d commits", i, len(commits))

        if not commit.parent_hash:
            continue

        if dry_run:
            modified = get_modified_files(
                repo_path, commit.parent_hash, commit.hash, analyzer
            )
            if modified:
                logger.info(
                    "  [dry-run] %s: %d proof files modified",
                    commit.hash[:8],
                    len(modified),
                )
            continue

        challenges = mine_commit(repo_path, commit, analyzer, metadata.name)
        if challenges:
            logger.info(
                "  %s: %d challenges found", commit.hash[:8], len(challenges)
            )
            all_challenges.extend(challenges)

    return MiningResult(
        repo_name=metadata.name,
        proof_assistant=analyzer.proof_assistant,
        total_commits_scanned=len(commits),
        total_challenges=len(all_challenges),
        challenges=all_challenges,
    )
