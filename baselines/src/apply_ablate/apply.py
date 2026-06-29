"""Materialise an ablation onto disk: symlink-overlay SRC->DST and write one real file.

`challenge_file_content` is the *full* ablated file text, so applying a challenge is
writing it at `DST/<file_path>`. To keep per-challenge setup cheap, `apply_record` does
not copy the repo — it builds a symlink overlay (`overlay_repo`): every file/dir is a
symlink back to SRC except the one edited file (and the dirs along its path), which are
real. The record's `file_path` is repo-relative (after the ablator's `-d` prefix-strip);
if it does not resolve directly we fall back to a unique basename search.
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


def overlay_repo(
    src: Path,
    dst: Path,
    target_rel: Path,
    *,
    overwrite: bool,
    include_git: bool = False,
) -> None:
    """Mirror `src` into `dst` with **symlinks**, materialising only the directories
    along `target_rel` as real dirs — so the caller can write a real file at
    `dst/target_rel` without touching `src`, and every other file/dir is a symlink back
    to `src` (no bulk copy).

    This avoids duplicating large in-place build trees per check: Coq's `.vo` live
    beside the `.v` under `src/`, so a plain copy duplicates the whole repo every
    challenge. Single-file checks (`coqc`, `lake env lean`, `isabelle build`) only read
    the deps, so symlinks resolve the load path while the one edited file stays real.
    """
    if not src.is_dir():
        raise ApplyError(f"source directory does not exist: {src}")
    if dst.exists():
        if overwrite:
            shutil.rmtree(dst)
        elif any(dst.iterdir()):
            raise ApplyError(
                f"destination {dst} is not empty (pass --overwrite to replace it)"
            )
    skip = set() if include_git else {".git"}
    # directories that must be real (every ancestor dir of the target)
    real_dirs: set[Path] = set()
    cur = Path()
    for part in target_rel.parts[:-1]:
        cur = cur / part
        real_dirs.add(cur)

    def build(rel: Path) -> None:
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for entry in sorted((src / rel).iterdir()):
            er = rel / entry.name
            if er == target_rel or entry.name in skip:
                continue  # target is written real by the caller; skip ignored names
            if entry.is_dir() and er in real_dirs:
                build(er)
            else:
                (dst / er).symlink_to(entry.resolve())

    build(Path())


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
    """Overlay SRC->DST (symlinks) and write the challenge (or recovered solution) at
    the target path in DST.

    Returns the absolute path of the file that was written. Uses a symlink overlay
    (see `overlay_repo`) so per-challenge setup is O(files-along-path), not a full copy.
    """
    # resolve the target's path relative to the pristine source, then overlay
    direct = src / record.file_path
    if direct.is_file():
        target_rel = Path(record.file_path)
    else:
        target_rel = resolve_target(src, record.file_path).relative_to(src)
    overlay_repo(src, dst, target_rel, overwrite=overwrite, include_git=include_git)
    target = dst / target_rel
    content = record.solution_text() if solution else record.challenge_file_content
    target.parent.mkdir(parents=True, exist_ok=True)
    write_content(target, content)
    return target
