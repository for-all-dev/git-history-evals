"""Shared fixtures for scaffold tests."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from scaffold.analyzers import ProfileAnalyzer
from scaffold.profile import HoleMarker, RepoProfile

_COQ_DECL = r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Fact|Remark|Example|Definition|Fixpoint|Program)\s+(\w+)"


@pytest.fixture
def coq_profile() -> RepoProfile:
    return RepoProfile(
        proof_assistant="coq",
        proof_file_globs=["*.v"],
        hole_markers=[
            HoleMarker(regex=r"\bAdmitted\b", kind="admitted"),
            HoleMarker(regex=r"\badmit\b", kind="admit"),
        ],
        declaration_patterns=[_COQ_DECL],
    )


@pytest.fixture
def isabelle_profile() -> RepoProfile:
    return RepoProfile(
        proof_assistant="isabelle",
        proof_file_globs=["*.thy"],
        hole_markers=[
            HoleMarker(regex=r"\bsorry\b", kind="sorry"),
            HoleMarker(regex=r"\boops\b", kind="oops"),
        ],
        declaration_patterns=[
            r"^\s*(?:theorem|lemma|corollary|proposition|schematic_goal)\s+(\w+)"
        ],
    )


@pytest.fixture
def lean_profile() -> RepoProfile:
    return RepoProfile(
        proof_assistant="lean4",
        proof_file_globs=["*.lean"],
        hole_markers=[HoleMarker(regex=r"\bsorry\b", kind="sorry")],
        declaration_patterns=[r"^\s*(?:theorem|lemma|def|instance)\s+(\w+)"],
    )


@pytest.fixture
def coq_analyzer(coq_profile: RepoProfile) -> ProfileAnalyzer:
    return ProfileAnalyzer(coq_profile.compiled())


@pytest.fixture
def isabelle_analyzer(isabelle_profile: RepoProfile) -> ProfileAnalyzer:
    return ProfileAnalyzer(isabelle_profile.compiled())


@pytest.fixture
def lean_analyzer(lean_profile: RepoProfile) -> ProfileAnalyzer:
    return ProfileAnalyzer(lean_profile.compiled())


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with a known commit history for testing."""
    repo = tmp_path / "test-repo"
    repo.mkdir()

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

    # Commit 1: Coq file with Admitted
    proof_v = repo / "proof.v"
    proof_v.write_text(
        textwrap.dedent("""\
        Theorem add_comm : forall n m : nat, n + m = m + n.
        Proof.
          Admitted.
    """)
    )
    git("add", "proof.v")
    git("commit", "-m", "Add theorem with Admitted")

    # Commit 2: Fill in the proof
    proof_v.write_text(
        textwrap.dedent("""\
        Theorem add_comm : forall n m : nat, n + m = m + n.
        Proof.
          intros n m.
          induction n as [| n' IHn'].
          - simpl. rewrite Nat.add_0_r. reflexivity.
          - simpl. rewrite IHn'. rewrite Nat.add_succ_r. reflexivity.
        Qed.
    """)
    )
    git("add", "proof.v")
    git("commit", "-m", "Complete proof of add_comm")

    # Commit 3: Add another theorem with Admitted
    helpers_v = repo / "helpers.v"
    helpers_v.write_text(
        textwrap.dedent("""\
        Lemma add_0_r : forall n : nat, n + 0 = n.
        Proof.
          admit.
        Admitted.
    """)
    )
    git("add", "helpers.v")
    git("commit", "-m", "Add helper lemma (incomplete)")

    return repo


@pytest.fixture
def sample_coq_with_holes() -> str:
    return textwrap.dedent("""\
        Theorem foo : forall n, n = n.
        Proof.
          Admitted.

        Lemma bar : forall n m, n + m = m + n.
        Proof.
          intros.
          admit.
        Admitted.
    """)


@pytest.fixture
def sample_coq_filled() -> str:
    return textwrap.dedent("""\
        Theorem foo : forall n, n = n.
        Proof.
          reflexivity.
        Qed.

        Lemma bar : forall n m, n + m = m + n.
        Proof.
          intros.
          apply Nat.add_comm.
        Qed.
    """)


@pytest.fixture
def sample_isabelle_with_holes() -> str:
    return textwrap.dedent("""\
        theory Test
        imports Main
        begin

        theorem test_thm: "True"
          sorry

        lemma helper: "1 + 1 = 2"
          oops

        end
    """)


@pytest.fixture
def sample_lean_with_holes() -> str:
    return textwrap.dedent("""\
        theorem test_thm : True := by
          sorry

        lemma helper : 1 + 1 = 2 := by
          sorry
    """)
