"""Tests for git history walker using temp git repos."""

from __future__ import annotations

from pathlib import Path

from scaffold.analyzers.coq import CoqAnalyzer
from scaffold.git_walker import (
    get_file_at_commit,
    get_modified_files,
    iter_commits,
    mine_commit,
)


class TestGitWalker:
    def test_iter_commits(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        assert len(commits) == 3

    def test_iter_commits_with_limit(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo, max_commits=2)
        assert len(commits) == 2

    def test_get_file_at_commit(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        # Most recent commit (index 0) added helpers.v
        content = get_file_at_commit(tmp_git_repo, commits[0].hash, "helpers.v")
        assert content is not None
        assert "admit" in content

    def test_get_file_at_commit_not_found(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        content = get_file_at_commit(tmp_git_repo, commits[0].hash, "nonexistent.v")
        assert content is None

    def test_get_modified_files(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        analyzer = CoqAnalyzer()
        # Second commit (index 1) modified proof.v
        modified = get_modified_files(
            tmp_git_repo, commits[1].parent_hash, commits[1].hash, analyzer
        )
        assert "proof.v" in modified

    def test_mine_commit_finds_filled_holes(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        analyzer = CoqAnalyzer()
        # Second commit (index 1) filled the Admitted in proof.v
        challenges = mine_commit(tmp_git_repo, commits[1], analyzer, "test-repo")
        assert len(challenges) == 1
        challenge = challenges[0]
        assert challenge.file_path == "proof.v"
        assert "Admitted" in challenge.challenge_file_content
        assert "Qed" in challenge.solution_file_content
        assert len(challenge.holes_filled) > 0

    def test_mine_commit_no_parent(self, tmp_git_repo: Path) -> None:
        commits = iter_commits(tmp_git_repo)
        analyzer = CoqAnalyzer()
        # Last commit (oldest, index 2) has no parent
        initial = commits[-1]
        assert initial.parent_hash == ""
        challenges = mine_commit(tmp_git_repo, initial, analyzer, "test-repo")
        assert challenges == []
