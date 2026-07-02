"""Tests for the copy + resolve + write logic in apply.py."""

from pathlib import Path

import pytest

from apply_ablate.apply import (
    ApplyError,
    apply_record,
    copy_repo,
    find_lemma_user_files,
    overlay_repo,
    resolve_target,
)
from apply_ablate.record import AblationRecord, load_record


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


# --- withholding files that use a deleted lemma ---


def _lemma_src(tmp_path: Path) -> Path:
    """A src tree: target (Ablated.lean) + a downstream user + an unrelated file, in a
    sibling directory to exercise the deep-exclude path."""
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "b").mkdir(parents=True)
    (src / "a" / "Ablated.lean").write_text("theorem helper : True := trivial\n")
    (src / "b" / "User.lean").write_text("-- uses helper\nexample := helper\n")
    (src / "b" / "Unrelated.lean").write_text("-- nothing to see\n")
    return src


def test_find_lemma_user_files_matches_users_only(tmp_path: Path):
    src = _lemma_src(tmp_path)
    found = find_lemma_user_files(
        src, ["helper"], Path("a/Ablated.lean"), skip={".git"}
    )
    assert found == {Path("b/User.lean")}


def test_find_lemma_user_files_empty_when_no_names(tmp_path: Path):
    src = _lemma_src(tmp_path)
    assert (
        find_lemma_user_files(src, [], Path("a/Ablated.lean"), skip={".git"}) == set()
    )


def _lemma_record() -> AblationRecord:
    return AblationRecord.model_validate(
        {
            "proof_assistant": "lean",
            "file_path": "a/Ablated.lean",
            "challenge_file_content": "theorem helper : True := by sorry\n",
            "solution_file_content": "theorem helper : True := trivial\n",
            "deleted_lemmas": [{"name": "helper", "text": "theorem helper ..."}],
        }
    )


def test_apply_record_withholds_lemma_users(tmp_path: Path):
    src = _lemma_src(tmp_path)
    dst = tmp_path / "dst"
    apply_record(_lemma_record(), src, dst, overwrite=False)
    # the target is written real; the downstream user is withheld; siblings survive
    assert (dst / "a" / "Ablated.lean").exists()
    assert not (dst / "b" / "User.lean").exists()
    assert (dst / "b" / "Unrelated.lean").exists()


def test_apply_record_withhold_disabled(tmp_path: Path):
    src = _lemma_src(tmp_path)
    dst = tmp_path / "dst"
    apply_record(_lemma_record(), src, dst, overwrite=False, withhold_lemma_users=False)
    assert (dst / "b" / "User.lean").exists()  # present when disabled


def test_apply_record_drops_target_vo_symlink(tmp_path: Path):
    """The target's own .vo/.glob must NOT remain a symlink into src, else compiling
    the challenge writes through the symlink and clobbers the pristine src .vo."""
    src = tmp_path / "src"
    (src / "Util").mkdir(parents=True)
    (src / "Util" / "NatUtil.v").write_text("(* orig *)\n")
    (src / "Util" / "NatUtil.vo").write_bytes(b"PRISTINE-VO")
    (src / "Util" / "NatUtil.glob").write_text("glob\n")
    rec = AblationRecord.model_validate(
        {
            "proof_assistant": "coq",
            "file_path": "Util/NatUtil.v",
            "challenge_file_content": "(* holed *)\nProof. Admitted.\n",
            "solution_file_content": "(* solved *)\nProof. reflexivity. Qed.\n",
            "deleted_lemmas": [],
        }
    )
    dst = tmp_path / "dst"
    target = apply_record(rec, src, dst, overwrite=False)
    vo = dst / "Util" / "NatUtil.vo"
    assert not vo.exists() and not vo.is_symlink()  # companion dropped
    assert not (dst / "Util" / "NatUtil.glob").is_symlink()
    # simulate the compiler writing a fresh .vo next to the challenge
    vo.write_bytes(b"FRESH-VO")
    assert (src / "Util" / "NatUtil.vo").read_bytes() == b"PRISTINE-VO"  # src intact
    assert target.read_text().startswith("(* holed *)")


def test_overlay_exclude_deep_sibling(tmp_path: Path):
    src = _lemma_src(tmp_path)
    dst = tmp_path / "dst"
    overlay_repo(
        src,
        dst,
        Path("a/Ablated.lean"),
        overwrite=False,
        exclude=frozenset({Path("b/User.lean")}),
    )
    assert not (dst / "b" / "User.lean").exists()
    assert (dst / "b" / "Unrelated.lean").exists()  # symlinked
    assert (dst / "b" / "Unrelated.lean").is_symlink()
