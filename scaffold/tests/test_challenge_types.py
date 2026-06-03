"""Tests for proof_optimise, proof_add, and spec_change challenge formulations."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from scaffold.analyzers import ProfileAnalyzer
from scaffold.git_walker import (
    iter_commits,
    mine_from_enriched,
    mine_proof_add_commit,
    mine_proof_optimise_commit,
    mine_spec_change_commit,
    parse_decl_spans,
    splice_spec_change,
)
from scaffold.models import ChallengeType, CommitClass, CommitRecord
from scaffold.profile import HoleMarker, RepoProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COQ_DECL = r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example|Definition|Fixpoint|Program)\s+(\w+)"


def _coq_profile() -> RepoProfile:
    return RepoProfile(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[
            HoleMarker(regex=r"\bAdmitted\b", kind="admitted"),
            HoleMarker(regex=r"\badmit\b", kind="admit"),
        ],
        declaration_patterns=[_COQ_DECL],
    )


def _git_env() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        env=_git_env(),
    )


def _make_commit_record(
    repo: Path,
    commit_class: CommitClass,
    commit_idx: int = 0,
) -> CommitRecord:
    """Build a CommitRecord from a real git commit in ``repo``."""
    commits = iter_commits(repo)
    c = commits[commit_idx]
    parent = c.parent_hash
    # Get changed files.
    result = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", parent, c.hash],
        capture_output=True,
        text=True,
        check=True,
    )
    changed = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    proof_files = [f for f in changed if f.endswith(".v")]
    return CommitRecord(
        hash=c.hash,
        parent_hashes=[parent] if parent else [],
        author=c.author,
        date=c.date,
        message_subject=c.message,
        proof_files_changed=proof_files,
        touches_proof_files=bool(proof_files),
        commit_class=commit_class,
        class_confidence="test",
    )


# ---------------------------------------------------------------------------
# parse_decl_spans / splice_spec_change unit tests
# ---------------------------------------------------------------------------


class TestParseDeclSpans:
    def test_simple_theorem(self) -> None:
        content = textwrap.dedent("""\
            Theorem foo : forall n, n = n.
            Proof.
              reflexivity.
            Qed.
        """)
        profile = _coq_profile()
        spans = parse_decl_spans(content, profile.compiled().declaration_res)
        assert len(spans) == 1
        assert spans[0].name == "foo"
        assert spans[0].proof_start == 1  # "Proof." is line index 1
        assert "reflexivity" in spans[0].proof_body

    def test_multiple_decls(self) -> None:
        content = textwrap.dedent("""\
            Theorem foo : True.
            Proof.
              trivial.
            Qed.

            Lemma bar : 1 = 1.
            Proof.
              reflexivity.
            Qed.
        """)
        profile = _coq_profile()
        spans = parse_decl_spans(content, profile.compiled().declaration_res)
        assert len(spans) == 2
        assert spans[0].name == "foo"
        assert spans[1].name == "bar"

    def test_no_proof_keyword(self) -> None:
        content = textwrap.dedent("""\
            Theorem foo : True.
              trivial.
            Qed.
        """)
        profile = _coq_profile()
        spans = parse_decl_spans(content, profile.compiled().declaration_res)
        # Should still parse — proof_start is None.
        assert len(spans) == 1
        assert spans[0].name == "foo"
        assert spans[0].proof_start is None

    def test_empty_content(self) -> None:
        profile = _coq_profile()
        spans = parse_decl_spans("", profile.compiled().declaration_res)
        assert spans == []


class TestSpliceSpecChange:
    def test_basic_splice(self) -> None:
        parent = textwrap.dedent("""\
            Theorem foo : forall n, n = n.
            Proof.
              reflexivity.
            Qed.
        """)
        child = textwrap.dedent("""\
            Theorem foo : forall n m, n + m = m + n.
            Proof.
              intros. apply Nat.add_comm.
            Qed.
        """)
        profile = _coq_profile()
        result = splice_spec_change(parent, child, profile.compiled().declaration_res)
        assert result is not None
        spliced, names = result
        assert "foo" in names
        # Spliced should have new statement + old proof body.
        assert "forall n m" in spliced  # new statement
        assert "reflexivity" in spliced  # old proof body

    def test_no_change_returns_none(self) -> None:
        content = textwrap.dedent("""\
            Theorem foo : True.
            Proof.
              trivial.
            Qed.
        """)
        profile = _coq_profile()
        result = splice_spec_change(
            content, content, profile.compiled().declaration_res
        )
        assert result is None

    def test_new_decl_not_in_parent(self) -> None:
        parent = textwrap.dedent("""\
            Theorem foo : True.
            Proof.
              trivial.
            Qed.
        """)
        child = textwrap.dedent("""\
            Theorem foo : True.
            Proof.
              trivial.
            Qed.

            Theorem bar : False.
            Proof.
              admit.
            Admitted.
        """)
        profile = _coq_profile()
        result = splice_spec_change(parent, child, profile.compiled().declaration_res)
        # bar is new, not changed — no spec change.
        assert result is None


# ---------------------------------------------------------------------------
# Git-based integration tests for each challenge type
# ---------------------------------------------------------------------------


@pytest.fixture
def proof_optimise_repo(tmp_path: Path) -> Path:
    """Git repo with a proof that gets shortened."""
    repo = tmp_path / "opt-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")

    # Commit 1: verbose proof.
    (repo / "proof.v").write_text(
        textwrap.dedent("""\
            Theorem foo : forall n, n = n.
            Proof.
              intros n.
              destruct n.
              - reflexivity.
              - reflexivity.
            Qed.
        """)
    )
    _git(repo, "add", "proof.v")
    _git(repo, "commit", "-m", "Add verbose proof")

    # Commit 2: shorter proof.
    (repo / "proof.v").write_text(
        textwrap.dedent("""\
            Theorem foo : forall n, n = n.
            Proof.
              reflexivity.
            Qed.
        """)
    )
    _git(repo, "add", "proof.v")
    _git(repo, "commit", "-m", "Simplify proof")

    return repo


@pytest.fixture
def proof_add_repo(tmp_path: Path) -> Path:
    """Git repo where a proof is added to an existing file."""
    repo = tmp_path / "add-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")

    # Commit 1: file with just a statement and Admitted.
    (repo / "defs.v").write_text(
        textwrap.dedent("""\
            Definition id (n : nat) := n.
        """)
    )
    _git(repo, "add", "defs.v")
    _git(repo, "commit", "-m", "Add definitions")

    # Commit 2: add a theorem with proof.
    (repo / "defs.v").write_text(
        textwrap.dedent("""\
            Definition id (n : nat) := n.

            Theorem id_correct : forall n, id n = n.
            Proof.
              intros n. unfold id. reflexivity.
            Qed.
        """)
    )
    _git(repo, "add", "defs.v")
    _git(repo, "commit", "-m", "Prove id_correct")

    return repo


@pytest.fixture
def spec_change_repo(tmp_path: Path) -> Path:
    """Git repo where a theorem statement is changed and proof adapted."""
    repo = tmp_path / "spec-repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")

    # Commit 1: original statement + proof.
    (repo / "spec.v").write_text(
        textwrap.dedent("""\
            Theorem foo : forall n, n = n.
            Proof.
              reflexivity.
            Qed.
        """)
    )
    _git(repo, "add", "spec.v")
    _git(repo, "commit", "-m", "Original spec")

    # Commit 2: changed statement + adapted proof.
    (repo / "spec.v").write_text(
        textwrap.dedent("""\
            Theorem foo : forall n m, n + m = m + n.
            Proof.
              intros. apply Nat.add_comm.
            Qed.
        """)
    )
    _git(repo, "add", "spec.v")
    _git(repo, "commit", "-m", "Generalize foo")

    return repo


class TestMineProofOptimise:
    def test_produces_challenge(self, proof_optimise_repo: Path) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(
            proof_optimise_repo, CommitClass.proof_optimise, commit_idx=0
        )
        challenges = mine_proof_optimise_commit(
            proof_optimise_repo, record, analyzer, "test"
        )
        assert len(challenges) == 1
        c = challenges[0]
        assert c.challenge_type == ChallengeType.proof_optimise
        assert "Simplify" in c.instructions
        # Challenge (parent) should have the longer proof.
        assert "destruct" in c.challenge_file_content
        # Solution (child) should have the shorter proof.
        assert "reflexivity" in c.solution_file_content
        assert "destruct" not in c.solution_file_content

    def test_no_parent_returns_empty(self) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = CommitRecord(
            hash="abc123",
            parent_hashes=[],
            date="2024-01-01",
            message_subject="test",
            commit_class=CommitClass.proof_optimise,
        )
        challenges = mine_proof_optimise_commit("/fake", record, analyzer, "test")
        assert challenges == []


class TestMineProofAdd:
    def test_produces_challenge(self, proof_add_repo: Path) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(
            proof_add_repo, CommitClass.proof_add, commit_idx=0
        )
        challenges = mine_proof_add_commit(proof_add_repo, record, analyzer, "test")
        assert len(challenges) == 1
        c = challenges[0]
        assert c.challenge_type == ChallengeType.proof_add
        assert "Write or extend" in c.instructions
        # Solution should contain the proof.
        assert "id_correct" in c.solution_file_content

    def test_new_file_produces_challenge(self, tmp_path: Path) -> None:
        """When the file didn't exist in the parent, challenge_file_content is empty."""
        repo = tmp_path / "newfile-repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "checkout", "-b", "main")

        (repo / "dummy.v").write_text("(* placeholder *)\n")
        _git(repo, "add", "dummy.v")
        _git(repo, "commit", "-m", "Init")

        (repo / "newproof.v").write_text(
            textwrap.dedent("""\
                Theorem bar : True.
                Proof.
                  trivial.
                Qed.
            """)
        )
        _git(repo, "add", "newproof.v")
        _git(repo, "commit", "-m", "Add new proof file")

        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(repo, CommitClass.proof_add, commit_idx=0)
        challenges = mine_proof_add_commit(repo, record, analyzer, "test")
        # Should produce a challenge for newproof.v.
        new_challenges = [c for c in challenges if c.file_path == "newproof.v"]
        assert len(new_challenges) == 1
        assert new_challenges[0].challenge_file_content == ""
        assert "Write the proof" in new_challenges[0].instructions


class TestMineSpecChange:
    def test_produces_challenge(self, spec_change_repo: Path) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(
            spec_change_repo, CommitClass.spec_change, commit_idx=0
        )
        challenges = mine_spec_change_commit(spec_change_repo, record, analyzer, "test")
        assert len(challenges) == 1
        c = challenges[0]
        assert c.challenge_type == ChallengeType.spec_change
        assert "`foo`" in c.instructions
        assert "modified" in c.instructions
        # Challenge should have new statement spliced with old proof.
        assert "forall n m" in c.challenge_file_content  # new statement
        assert "reflexivity" in c.challenge_file_content  # old proof body
        # Solution should have the fully adapted proof.
        assert "Nat.add_comm" in c.solution_file_content

    def test_no_spec_change_returns_empty(self, proof_optimise_repo: Path) -> None:
        """No spec change → no challenges from this miner."""
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(
            proof_optimise_repo, CommitClass.spec_change, commit_idx=0
        )
        challenges = mine_spec_change_commit(
            proof_optimise_repo, record, analyzer, "test"
        )
        assert challenges == []


# ---------------------------------------------------------------------------
# mine_from_enriched integration test
# ---------------------------------------------------------------------------


class TestMineFromEnriched:
    def test_dispatches_by_class(
        self,
        proof_optimise_repo: Path,
        proof_add_repo: Path,
        spec_change_repo: Path,
    ) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())

        # Build records from each repo.
        opt_record = _make_commit_record(
            proof_optimise_repo, CommitClass.proof_optimise
        )
        add_record = _make_commit_record(proof_add_repo, CommitClass.proof_add)
        spec_record = _make_commit_record(spec_change_repo, CommitClass.spec_change)

        # mine_from_enriched needs all records to point at the same repo,
        # so we test each type individually.
        opt_result = mine_from_enriched(
            proof_optimise_repo,
            "test",
            analyzer,
            [opt_record],
        )
        assert opt_result.total_challenges >= 1
        assert all(
            c.challenge_type == ChallengeType.proof_optimise
            for c in opt_result.challenges
        )

        add_result = mine_from_enriched(
            proof_add_repo,
            "test",
            analyzer,
            [add_record],
        )
        assert add_result.total_challenges >= 1
        assert all(
            c.challenge_type == ChallengeType.proof_add for c in add_result.challenges
        )

        spec_result = mine_from_enriched(
            spec_change_repo,
            "test",
            analyzer,
            [spec_record],
        )
        assert spec_result.total_challenges >= 1
        assert all(
            c.challenge_type == ChallengeType.spec_change
            for c in spec_result.challenges
        )

    def test_class_filter(self, proof_optimise_repo: Path) -> None:
        """Only mine the requested classes."""
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(proof_optimise_repo, CommitClass.proof_optimise)

        # Ask for proof_add only — should skip proof_optimise.
        result = mine_from_enriched(
            proof_optimise_repo,
            "test",
            analyzer,
            [record],
            classes={"proof_add"},
        )
        assert result.total_challenges == 0

    def test_skips_unknown_classes(self, proof_optimise_repo: Path) -> None:
        profile = _coq_profile()
        analyzer = ProfileAnalyzer(profile.compiled())
        record = _make_commit_record(
            proof_optimise_repo,
            CommitClass.infra,  # not a mineable class
        )
        result = mine_from_enriched(
            proof_optimise_repo,
            "test",
            analyzer,
            [record],
        )
        assert result.total_challenges == 0
