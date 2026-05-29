"""Tests for calibration agent host-side tools."""

from __future__ import annotations

from typing import Callable
from pathlib import Path

import pytest

from scaffold.profile import RepoProfile, load_profile
from scaffold.profiler.deps import ProfilerDeps
from scaffold.profiler.runner import _checkpoint_calibration
from scaffold.profiler.tools import build_tools


_MINIMAL_PROFILE = {
    "proof_assistant": "coq",
    "proof_file_globs": ["*.v"],
    "hole_markers": [{"regex": r"\bAdmitted\b", "kind": "admitted"}],
    "declaration_patterns": [r"^\s*(?:Theorem|Lemma|Definition)\s+(\w+)"],
}


@pytest.fixture
def deps(tmp_git_repo: Path) -> ProfilerDeps:
    """Fixture to create ProfilerDeps for the temporary git repository."""
    return ProfilerDeps(repo_path=tmp_git_repo)


@pytest.fixture
def tools(deps: ProfilerDeps) -> dict[str, Callable]:
    """Fixture to build the profiler tools closed over the deps."""
    return build_tools(deps)


def test_list_files_globs(tools: dict[str, Callable]) -> None:
    """Test that list_files filters correctly with globs."""
    list_files = tools["list_files"]
    v_files = list_files("*.v")
    assert set(v_files) == {"proof.v", "helpers.v"}

    thy_files = list_files("*.thy")
    assert thy_files == []


def test_read_file_slice(tools: dict[str, Callable]) -> None:
    """Test that read_file returns a correct slice of the file."""
    read_file = tools["read_file"]
    content = read_file("proof.v", 1, 1)
    assert "Theorem add_comm" in content


def test_read_file_rejects_escape(tools: dict[str, Callable]) -> None:
    """Test that read_file rejects path traversal attempts."""
    read_file = tools["read_file"]
    with pytest.raises(ValueError, match="escapes repo root"):
        read_file("../etc/passwd")


def test_git_log_returns_commits(tools: dict[str, Callable]) -> None:
    """Test that git_log returns the expected commits in order."""
    git_log = tools["git_log"]
    commits = git_log(n=10)
    assert len(commits) == 3
    assert commits[0]["subject"] == "Add helper lemma (incomplete)"


def test_git_log_grep(tools: dict[str, Callable]) -> None:
    """Test that git_log can search/grep commit messages."""
    git_log = tools["git_log"]
    commits = git_log(n=10, grep="^Complete")
    assert len(commits) == 1
    assert commits[0]["subject"] == "Complete proof of add_comm"


def test_count_regex_admitted(tools: dict[str, Callable]) -> None:
    """Test count_regex on a valid pattern."""
    count_regex = tools["count_regex"]
    res = count_regex(r"\bAdmitted\b", "*.v")
    assert res["total_matches"] == 1
    assert res["files_with_match"] == 1


def test_count_regex_bad_pattern(tools: dict[str, Callable]) -> None:
    """Test that count_regex handles an invalid regex pattern without raising."""
    count_regex = tools["count_regex"]
    res = count_regex("(")
    assert res["total_matches"] == 0
    assert "error" in res
    assert res["error"] != ""


def test_test_profile_mines_challenge(tools: dict[str, Callable]) -> None:
    """Test that test_profile runs the mining engine and returns challenges."""
    test_profile = tools["test_profile"]
    profile = {
        "proof_assistant": "coq",
        "proof_file_globs": ["*.v"],
        "hole_markers": [
            {"regex": r"\bAdmitted\b", "kind": "admitted"},
            {"regex": r"\badmit\b", "kind": "admit"},
        ],
        "declaration_patterns": [r"^\s*(?:Theorem|Lemma|Definition)\s+(\w+)"],
    }
    res = test_profile(profile)
    assert res["ok"] is True
    assert res["challenges_mined"] >= 1
    assert isinstance(res["example_challenges"], list)
    assert len(res["example_challenges"]) > 0


def test_test_profile_rejects_bad_profile(tools: dict[str, Callable]) -> None:
    """Test that test_profile rejects an invalid profile gracefully."""
    test_profile = tools["test_profile"]
    res = test_profile({"proof_assistant": "coq"})
    assert res["ok"] is False
    assert "error" in res
    assert isinstance(res["error"], str)
    assert res["error"] != ""


def test_deps_log_records_calls(tools: dict[str, Callable], deps: ProfilerDeps) -> None:
    """Test that calling tools records records to deps.log."""
    assert len(deps.log) == 0
    tools["list_files"]("*.v")
    tools["read_file"]("proof.v", 1, 1)
    assert len(deps.log) > 0


def test_checkpoint_calibration_persists_recoverable_profile(tmp_path: Path) -> None:
    """The checkpoint writes a profile.json + transcript.txt that round-trips.

    This is the crash-recovery guarantee: the calibrated profile must land on
    disk (and reload as a valid ``RepoProfile``) independently of the later mine.
    """
    profile = RepoProfile.model_validate(_MINIMAL_PROFILE)
    ckpt = _checkpoint_calibration(
        profile=profile,
        transcript=["list_files glob='*.v' matched=2/2", "test_profile commits=10"],
        repo_path=tmp_path / "myrepo",
        tag="agent",
        artifacts_root=tmp_path / "artifacts",
        created_at="2026-05-29T02:09:35.120000+00:00",
    )

    assert (
        ckpt
        == tmp_path
        / "artifacts"
        / "myrepo-eval"
        / "_checkpoints"
        / "agent-20260529T020935"
    )
    assert (ckpt / "profile.json").is_file()
    assert (ckpt / "transcript.txt").read_text().startswith("list_files")
    # The whole point: it reloads as a usable RepoProfile.
    recovered = load_profile(ckpt / "profile.json")
    assert recovered.proof_assistant == "coq"
    assert [h.kind for h in recovered.hole_markers] == ["admitted"]


def test_checkpoint_calibration_omits_empty_transcript(tmp_path: Path) -> None:
    """No transcript.txt is written when the transcript is empty."""
    ckpt = _checkpoint_calibration(
        profile=RepoProfile.model_validate(_MINIMAL_PROFILE),
        transcript=[],
        repo_path=tmp_path / "myrepo",
        tag="agent",
        artifacts_root=tmp_path / "artifacts",
        created_at="2026-05-29T02:09:35.120000+00:00",
    )
    assert (ckpt / "profile.json").is_file()
    assert not (ckpt / "transcript.txt").exists()
