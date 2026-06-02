"""Tests for git history walker using temp git repos."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scaffold.analyzers import ProfileAnalyzer
from scaffold.git_walker import (
    dump_commits,
    get_file_at_commit,
    get_modified_files,
    iter_commits,
    mine_commit,
)
from scaffold.profile import HoleMarker, RepoProfile


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

    def test_get_modified_files(
        self, tmp_git_repo: Path, coq_analyzer: ProfileAnalyzer
    ) -> None:
        commits = iter_commits(tmp_git_repo)
        # Second commit (index 1) modified proof.v
        modified = get_modified_files(
            tmp_git_repo, commits[1].parent_hash, commits[1].hash, coq_analyzer
        )
        assert "proof.v" in modified

    def test_mine_commit_finds_filled_holes(
        self, tmp_git_repo: Path, coq_analyzer: ProfileAnalyzer
    ) -> None:
        commits = iter_commits(tmp_git_repo)
        # Second commit (index 1) filled the Admitted in proof.v
        challenges = mine_commit(tmp_git_repo, commits[1], coq_analyzer, "test-repo")
        assert len(challenges) == 1
        challenge = challenges[0]
        assert challenge.file_path == "proof.v"
        assert "Admitted" in challenge.challenge_file_content
        assert "Qed" in challenge.solution_file_content
        assert len(challenge.holes_filled) > 0

    def test_mine_commit_no_parent(
        self, tmp_git_repo: Path, coq_analyzer: ProfileAnalyzer
    ) -> None:
        commits = iter_commits(tmp_git_repo)
        # Last commit (oldest, index 2) has no parent
        initial = commits[-1]
        assert initial.parent_hash == ""
        challenges = mine_commit(tmp_git_repo, initial, coq_analyzer, "test-repo")
        assert challenges == []


class TestExcludeGlobsIntegration:
    """Integration tests: exclude_globs filters vendored files in both code paths."""

    @staticmethod
    def _make_repo_with_vendor(tmp_path: Path) -> Path:
        """Create a git repo with both a regular and a vendored .v file."""
        repo = tmp_path / "exclude-repo"
        repo.mkdir()
        (repo / "vendor").mkdir()

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                check=True,
                env={
                    **os.environ,
                    "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "test@test.com",
                    "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "test@test.com",
                },
            )

        git("init")
        git("checkout", "-b", "main")

        # Commit 1: add both files with Admitted
        (repo / "src.v").write_text("Theorem t : True.\nProof.\n  Admitted.\n")
        (repo / "vendor" / "lib.v").write_text(
            "Theorem v : True.\nProof.\n  Admitted.\n"
        )
        git("add", "src.v", "vendor/lib.v")
        git("commit", "-m", "Initial with Admitted")

        # Commit 2: fill both proofs
        (repo / "src.v").write_text("Theorem t : True.\nProof.\n  trivial.\nQed.\n")
        (repo / "vendor" / "lib.v").write_text(
            "Theorem v : True.\nProof.\n  trivial.\nQed.\n"
        )
        git("add", "src.v", "vendor/lib.v")
        git("commit", "-m", "Complete proofs")

        return repo

    @staticmethod
    def _profile_with_exclude() -> RepoProfile:
        return RepoProfile(
            proof_assistant="coq",
            proof_file_globs=["*.v"],
            exclude_globs=["vendor/*"],
            hole_markers=[
                HoleMarker(regex=r"\bAdmitted\b", kind="admitted"),
            ],
            declaration_patterns=[
                r"^\s*(?:Theorem|Lemma)\s+(\w+)",
            ],
        )

    def test_get_modified_files_excludes_vendor(self, tmp_path: Path) -> None:
        repo = self._make_repo_with_vendor(tmp_path)
        profile = self._profile_with_exclude()
        analyzer = ProfileAnalyzer(profile.compiled())
        commits = iter_commits(repo)
        # commits[0] is the proof-filling commit
        modified = get_modified_files(
            repo, commits[0].parent_hash, commits[0].hash, analyzer
        )
        assert "src.v" in modified
        assert "vendor/lib.v" not in modified

    def test_dump_commits_excludes_vendor(self, tmp_path: Path) -> None:
        repo = self._make_repo_with_vendor(tmp_path)
        profile = self._profile_with_exclude()
        compiled = profile.compiled()
        records = dump_commits(repo, compiled)
        # The proof-filling commit should only list src.v as a proof file
        filling_commit = records[0]  # most recent
        assert "src.v" in filling_commit.proof_files_changed
        assert "vendor/lib.v" not in filling_commit.proof_files_changed

    def test_mine_commit_excludes_vendor(self, tmp_path: Path) -> None:
        repo = self._make_repo_with_vendor(tmp_path)
        profile = self._profile_with_exclude()
        analyzer = ProfileAnalyzer(profile.compiled())
        commits = iter_commits(repo)
        challenges = mine_commit(repo, commits[0], analyzer, "test-repo")
        file_paths = [c.file_path for c in challenges]
        assert "src.v" in file_paths
        assert "vendor/lib.v" not in file_paths
