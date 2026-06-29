"""Tests for the copy + resolve + write logic in apply.py."""

from pathlib import Path

import pytest

from apply_ablate.apply import (
    ApplyError,
    apply_record,
    copy_repo,
    resolve_target,
)
from apply_ablate.record import load_record


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "Foo.v").write_text("original\n")
    (src / "top.txt").write_text("top\n")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n")
    return src


def test_copy_repo_skips_git(tmp_path: Path):
    src = _make_src(tmp_path)
    dst = tmp_path / "dst"
    copy_repo(src, dst, overwrite=False)
    assert (dst / "sub" / "Foo.v").read_text() == "original\n"
    assert (dst / "top.txt").exists()
    assert not (dst / ".git").exists()


def test_copy_repo_include_git(tmp_path: Path):
    src = _make_src(tmp_path)
    dst = tmp_path / "dst"
    copy_repo(src, dst, overwrite=False, include_git=True)
    assert (dst / ".git" / "HEAD").exists()


def test_copy_repo_nonempty_dst_guard(tmp_path: Path):
    src = _make_src(tmp_path)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale").write_text("x")
    with pytest.raises(ApplyError, match="not empty"):
        copy_repo(src, dst, overwrite=False)
    copy_repo(src, dst, overwrite=True)
    assert not (dst / "stale").exists()


def test_resolve_target_direct(tmp_path: Path):
    root = tmp_path / "r"
    (root / "sub").mkdir(parents=True)
    f = root / "sub" / "Foo.v"
    f.write_text("x")
    assert resolve_target(root, "sub/Foo.v") == f


def test_resolve_target_basename_fallback(tmp_path: Path):
    root = tmp_path / "r"
    (root / "sub").mkdir(parents=True)
    f = root / "sub" / "Foo.v"
    f.write_text("x")
    # file_path is a bare basename not present at root -> unique rglob match
    assert resolve_target(root, "Foo.v") == f


def test_resolve_target_missing(tmp_path: Path):
    root = tmp_path / "r"
    root.mkdir()
    with pytest.raises(ApplyError, match="could not find"):
        resolve_target(root, "Nope.v")


def test_resolve_target_ambiguous(tmp_path: Path):
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "Foo.v").write_text("x")
    (root / "b" / "Foo.v").write_text("y")
    with pytest.raises(ApplyError, match="ambiguous"):
        resolve_target(root, "Foo.v")


def test_apply_record_writes_challenge(tmp_path: Path, fixture_jsonl: Path):
    rec = load_record(fixture_jsonl, 0)
    # build a src tree that contains the record's file_path
    src = tmp_path / "src"
    (src / Path(rec.file_path).parent).mkdir(parents=True, exist_ok=True)
    (src / rec.file_path).write_text("placeholder original\n")
    dst = tmp_path / "dst"
    written = apply_record(rec, src, dst, overwrite=False)
    assert written.read_text() == rec.challenge_file_content
    # src is untouched
    assert (src / rec.file_path).read_text() == "placeholder original\n"


def test_apply_record_writes_solution(tmp_path: Path, fixture_jsonl: Path):
    rec = load_record(fixture_jsonl, 0)
    src = tmp_path / "src"
    (src / Path(rec.file_path).parent).mkdir(parents=True, exist_ok=True)
    (src / rec.file_path).write_text("placeholder\n")
    dst = tmp_path / "dst"
    written = apply_record(rec, src, dst, overwrite=False, solution=True)
    assert written.read_text() == rec.solution_text()
