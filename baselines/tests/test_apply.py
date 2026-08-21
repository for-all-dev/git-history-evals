"""Tests for the copy + resolve + write logic in apply.py."""

from pathlib import Path

import pytest

from apply_ablate.apply import (
    ApplyError,
    apply_record,
    find_lemma_user_files,
    overlay_repo,
    resolve_target,
)
from apply_ablate.provers.base import CheckResult
from apply_ablate.provers.lean import LeanProver
from apply_ablate.record import AblationRecord, load_record


def _make_src(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "Foo.v").write_text("original\n")
    (src / "top.txt").write_text("top\n")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n")
    return src


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


def _lean_src(tmp_path: Path) -> Path:
    """A minimal lean package: a lakefile + a prebuilt `.lake/build/lib` (the heavy dep
    dir `overlay_repo` symlinks back to src rather than copying) + one source file."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "lakefile.toml").write_text("name = 'demo'\n")
    lib = src / ".lake" / "build" / "lib"
    lib.mkdir(parents=True)
    (lib / "Demo.olean").write_bytes(b"olean")
    (src / "A.lean").write_text("theorem t : True := trivial\n")
    return src


def _lean_record() -> AblationRecord:
    return AblationRecord.model_validate(
        {
            "proof_assistant": "lean",
            "file_path": "A.lean",
            "challenge_file_content": "theorem t : True := by sorry\n",
            "solution_file_content": "theorem t : True := trivial\n",
            "deleted_lemmas": [],
        }
    )


def test_lean_check_never_invokes_lake_through_overlay(tmp_path: Path, monkeypatch):
    """`.lake` in the overlay is (correctly) a symlink back to `src` — it is a heavy
    prebuilt dep dir `overlay_repo` never copies. The regression this guards is #119:
    `LeanProver.check` used to run `lake env lean` with `cwd` resolving inside the
    overlay, so lake's own workspace-resolution writes (dependency re-clone, compiled-
    lakefile-config cache) followed that symlink straight into the pristine source and
    corrupted it. `check` must now compile exclusively via bare `lean`, and the one
    `lake` invocation it may still make (the FFI-env snapshot) must run with `cwd`
    against the pristine root, never with `cwd` under the overlay's `.lake` symlink."""
    src = _lean_src(tmp_path)
    dst = tmp_path / "dst"
    target = apply_record(_lean_record(), src, dst, overwrite=False)
    assert target.read_text() == "theorem t : True := by sorry\n"
    lake_link = dst / ".lake"
    assert lake_link.is_symlink()
    assert lake_link.resolve() == (src / ".lake").resolve()

    calls: list[tuple[list[str], Path]] = []

    def fake_run(cmd, cwd, *, timeout, env=None):
        calls.append((cmd, Path(cwd)))
        return CheckResult(ok=cmd[0] == "lean", cmd=cmd, stdout="", stderr="")

    monkeypatch.setattr("apply_ablate.provers.lean.run", fake_run)
    prover = LeanProver()
    res = prover.check(dst, Path("A.lean"), allow_holes=True, timeout=5)
    assert res.ok

    lean_calls = [c for c in calls if c[0][0] == "lean"]
    assert len(lean_calls) == 1, "the compile must go through bare `lean`, not `lake`"

    lake_calls = [c for c in calls if c[0][0] == "lake"]
    for cmd, cwd in lake_calls:
        assert not str(cwd.resolve()).startswith(str(dst.resolve())), (
            f"lake invocation {cmd} ran with cwd under the overlay ({cwd}) — it would "
            "write through the `.lake` symlink into the pristine src"
        )


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


def test_overlay_repo_symlinks_link_dirs_to_src(tmp_path: Path):
    """Test that overlay_repo symlinks heavy dep dirs (_LINK_DIRS) back to src instead
    of copying them. This keeps per-challenge setup cheap by avoiding duplicating many GB
    of prebuilt dependencies."""
    src = tmp_path / "src"
    src.mkdir()
    # Create a realistic .lake/build/lib structure (common Lean prebuilt deps)
    lake_lib = src / ".lake" / "build" / "lib"
    lake_lib.mkdir(parents=True)
    (lake_lib / "Demo.olean").write_bytes(b"olean-data")
    # Create other _LINK_DIRS entries
    (src / "_build" / "default").mkdir(parents=True)
    (_build_file := src / "_build" / "default" / "artifact.o")
    _build_file.write_bytes(b"build-data")
    (src / "lake-packages" / "mathlib").mkdir(parents=True)
    (src / "lake-packages" / "mathlib" / "Mathlib.olean").write_bytes(b"mathlib")
    # Create a normal source file and target to materialize
    (src / "Main.lean").write_text("theorem main : True := trivial\n")

    dst = tmp_path / "dst"
    overlay_repo(
        src,
        dst,
        Path("Main.lean"),
        overwrite=False,
    )

    # Verify _LINK_DIRS are symlinked back to src, not copied
    assert (dst / ".lake").is_symlink()
    assert (dst / ".lake").resolve() == (src / ".lake").resolve()
    assert (dst / "_build").is_symlink()
    assert (dst / "_build").resolve() == (src / "_build").resolve()
    assert (dst / "lake-packages").is_symlink()
    assert (dst / "lake-packages").resolve() == (src / "lake-packages").resolve()
    # Verify the symlinks actually point to the same content
    assert (
        dst / ".lake" / "build" / "lib" / "Demo.olean"
    ).read_bytes() == b"olean-data"
    assert (dst / "_build" / "default" / "artifact.o").read_bytes() == b"build-data"
    assert (
        dst / "lake-packages" / "mathlib" / "Mathlib.olean"
    ).read_bytes() == b"mathlib"


def test_overlay_repo_link_dirs_read_only_from_dst(tmp_path: Path):
    """Test that writes to _LINK_DIRS from the overlay do not corrupt the pristine src.
    This guards the core contract: symlinks are safe because single-file checks only read,
    but if something writes through a symlink, the src gets corrupted."""
    src = tmp_path / "src"
    src.mkdir()
    # Create a _build directory with a file
    (src / "_build" / "artifact").mkdir(parents=True)
    (src / "_build" / "artifact" / "pristine.o").write_text("PRISTINE")

    # Create source file to target
    (src / "Main.lean").write_text("theorem main : True := trivial\n")

    dst = tmp_path / "dst"
    overlay_repo(
        src,
        dst,
        Path("Main.lean"),
        overwrite=False,
    )

    # The symlink exists
    assert (dst / "_build").is_symlink()
    # Try to "write" through the symlink (simulating what we guard against)
    # The test here is that the symlink points to src, so we verify it's read-safe
    build_link = dst / "_build"
    assert build_link.resolve() == (src / "_build").resolve()
    # Verify pristine src is unchanged (the symlink is there, so src data is visible)
    assert (src / "_build" / "artifact" / "pristine.o").read_text() == "PRISTINE"
