"""Materialise an ablation onto disk: copy SRC->DST and overwrite one file.

`challenge_file_content` is the *full* ablated file text, so applying a challenge is
simply writing it at `DST/<file_path>`. The record's `file_path` is repo-relative
(after the ablator's `-d` prefix-strip); if it does not resolve directly we fall back
to a unique basename search, erroring on a miss or ambiguity.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from apply_ablate.record import AblationRecord


class ApplyError(RuntimeError):
    """Raised when a destination cannot be prepared or a target cannot be resolved."""


# Heavy prebuilt-dependency dirs: never copied (can be many GB, e.g. a Lean `.lake`
# holds all of mathlib's `.olean`). They are symlinked back to `src` instead, which is
# safe because single-file checks (`lake env lean`, `coqc`) only *read* them.
_LINK_DIRS = (".lake", "lake-packages", "_build", "build")


def copy_repo(
    src: Path, dst: Path, *, overwrite: bool, include_git: bool = False
) -> None:
    """Copy the `src` tree to `dst`, skipping `.git` and symlinking heavy dep dirs."""
    if not src.is_dir():
        raise ApplyError(f"source directory does not exist: {src}")
    if dst.exists():
        if not overwrite:
            if any(dst.iterdir()):
                raise ApplyError(
                    f"destination {dst} is not empty (pass --overwrite to replace it)"
                )
        else:
            shutil.rmtree(dst)
    skip = ([] if include_git else [".git"]) + list(_LINK_DIRS)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(*skip),
        symlinks=True,
        dirs_exist_ok=True,
    )
    # Symlink the skipped heavy dep dirs back to the pristine source.
    for name in _LINK_DIRS:
        s = src / name
        if s.is_dir() and not (dst / name).exists():
            (dst / name).symlink_to(s.resolve())


def resolve_target(root: Path, file_path: str) -> Path:
    """Map a record's `file_path` to a concrete file under `root`.

    Prefers the literal repo-relative path; falls back to a unique-basename search.
    """
    direct = root / file_path
    if direct.is_file():
        return direct
    name = Path(file_path).name
    matches = [p for p in root.rglob(name) if p.is_file()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ApplyError(
            f"could not find {file_path!r} under {root} (no file named {name!r})"
        )
    raise ApplyError(
        f"ambiguous: {len(matches)} files named {name!r} under {root}; "
        f"record file_path {file_path!r} did not resolve directly"
    )


def write_content(target: Path, content: str) -> None:
    """Overwrite `target` with `content` (UTF-8, no added newline)."""
    target.write_text(content, encoding="utf-8")


def apply_record(
    record: AblationRecord,
    src: Path,
    dst: Path,
    *,
    overwrite: bool,
    solution: bool = False,
    include_git: bool = False,
) -> Path:
    """Copy SRC->DST and write the challenge (or recovered solution) into DST.

    Returns the absolute path of the file that was written.
    """
    copy_repo(src, dst, overwrite=overwrite, include_git=include_git)
    target = resolve_target(dst, record.file_path)
    content = record.solution_text() if solution else record.challenge_file_content
    write_content(target, content)
    return target
