"""Tests for the `--repo` manifest-aware slicing (issue #128)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from apply_ablate.baseline import _git_head, _select_indices, app
from apply_ablate.record import load_record

runner = CliRunner()


def _row(
    repo: str | None, revision: str | None = None, file_path: str = "A.lean"
) -> str:
    import json

    manifest = {}
    if repo is not None:
        manifest["repo"] = repo
    if revision is not None:
        manifest["revision"] = revision
    obj = {
        "proof_assistant": "lean",
        "file_path": file_path,
        "challenge_file_content": "theorem t : True := sorry\n",
    }
    if manifest:
        obj["manifest"] = manifest
    return json.dumps(obj)


def test_record_surfaces_manifest(tmp_path: Path):
    p = tmp_path / "one.jsonl"
    p.write_text(_row("foo", revision="deadbeef") + "\n")
    rec = load_record(p, 0)
    assert rec.manifest is not None
    assert rec.manifest.repo == "foo"
    assert rec.manifest.revision == "deadbeef"


def test_record_manifest_absent_is_none(tmp_path: Path):
    p = tmp_path / "one.jsonl"
    p.write_text(_row(None) + "\n")
    rec = load_record(p, 0)
    assert rec.manifest is None


def test_select_indices_single_repo_no_flag(tmp_path: Path):
    p = tmp_path / "single.jsonl"
    p.write_text("\n".join([_row("foo"), _row("foo")]) + "\n")
    indices, distinct = _select_indices(p, None)
    assert indices == [0, 1]
    assert distinct == {"foo"}


def test_select_indices_no_manifest_is_fine(tmp_path: Path):
    # legacy single-repo datasets predate the manifest field entirely.
    p = tmp_path / "legacy.jsonl"
    p.write_text("\n".join([_row(None), _row(None)]) + "\n")
    indices, distinct = _select_indices(p, None)
    assert indices == [0, 1]


def test_select_indices_repo_filter(tmp_path: Path):
    p = tmp_path / "mixed.jsonl"
    p.write_text("\n".join([_row("foo"), _row("bar"), _row("foo")]) + "\n")
    indices, distinct = _select_indices(p, "foo")
    assert indices == [0, 2]
    assert distinct == {"foo", "bar"}


def test_select_indices_repo_filter_no_match(tmp_path: Path):
    import typer

    p = tmp_path / "mixed.jsonl"
    p.write_text("\n".join([_row("foo"), _row("bar")]) + "\n")
    with pytest.raises(typer.Exit) as exc_info:
        _select_indices(p, "quux")
    assert exc_info.value.exit_code == 2


def test_select_indices_mixed_repo_without_flag_fails(tmp_path: Path):
    import typer

    p = tmp_path / "mixed.jsonl"
    p.write_text("\n".join([_row("foo"), _row("bar")]) + "\n")
    with pytest.raises(typer.Exit) as exc_info:
        _select_indices(p, None)
    assert exc_info.value.exit_code == 2


def test_cli_mixed_repo_without_flag_errors(tmp_path: Path):
    challenges = tmp_path / "mixed.jsonl"
    challenges.write_text("\n".join([_row("foo"), _row("bar")]) + "\n")
    src = tmp_path / "src"
    src.mkdir()
    result = runner.invoke(app, [str(challenges), str(src)])
    assert result.exit_code == 2
    assert "--repo" in result.output


def test_cli_repo_no_match_errors(tmp_path: Path):
    challenges = tmp_path / "one.jsonl"
    challenges.write_text(_row("foo") + "\n")
    src = tmp_path / "src"
    src.mkdir()
    result = runner.invoke(app, [str(challenges), str(src), "--repo", "quux"])
    assert result.exit_code == 2
    assert "quux" in result.output


def test_git_head_non_repo_is_none(tmp_path: Path):
    d = tmp_path / "not-a-repo"
    d.mkdir()
    assert _git_head(d) is None


def test_git_head_returns_sha(tmp_path: Path):
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True)
    head = _git_head(d)
    assert head is not None
    assert len(head) == 40


def _init_git_repo(d: Path) -> str:
    d.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=d, check=True)
    head = _git_head(d)
    assert head is not None
    return head


def test_cli_revision_mismatch_warns(tmp_path: Path, monkeypatch):
    head = _init_git_repo(tmp_path / "repo")
    src = tmp_path / "repo"

    challenges = tmp_path / "one.jsonl"
    challenges.write_text(_row("foo", revision="0" * 40) + "\n")

    def fake_solve_one(rec, src, work, **kwargs):
        from apply_ablate.solve import SolveResult

        return SolveResult(
            task_id=rec.task_id,
            assistant=rec.assistant,
            file_path=rec.file_path,
            succeeded=False,
            gave_up=False,
            dry_run=True,
        )

    monkeypatch.setattr("apply_ablate.baseline.solve_one", fake_solve_one)

    result = runner.invoke(
        app,
        [str(challenges), str(src), "--dry-run", "--out", str(tmp_path / "out.jsonl")],
    )
    assert result.exit_code == 0, result.output
    assert "warning" in result.output
    assert ("0" * 40) in result.output
    assert head in result.output


def test_cli_revision_match_no_warning(tmp_path: Path, monkeypatch):
    head = _init_git_repo(tmp_path / "repo")
    src = tmp_path / "repo"

    challenges = tmp_path / "one.jsonl"
    challenges.write_text(_row("foo", revision=head) + "\n")

    def fake_solve_one(rec, src, work, **kwargs):
        from apply_ablate.solve import SolveResult

        return SolveResult(
            task_id=rec.task_id,
            assistant=rec.assistant,
            file_path=rec.file_path,
            succeeded=False,
            gave_up=False,
            dry_run=True,
        )

    monkeypatch.setattr("apply_ablate.baseline.solve_one", fake_solve_one)

    result = runner.invoke(
        app,
        [str(challenges), str(src), "--dry-run", "--out", str(tmp_path / "out.jsonl")],
    )
    assert result.exit_code == 0, result.output
    assert "warning" not in result.output
