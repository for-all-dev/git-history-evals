"""Materialise an ablation onto disk: symlink-overlay SRC->DST and write one real file.

`challenge_file_content` is the *full* ablated file text, so applying a challenge is
writing it at `DST/<file_path>`. To keep per-challenge setup cheap, `apply_record` does
not copy the repo — it builds a symlink overlay (`overlay_repo`): every file/dir is a
symlink back to SRC except the one edited file (and the dirs along its path), which are
real. The record's `file_path` is repo-relative (after the ablator's `-d` prefix-strip);
if it does not resolve directly we fall back to a unique basename search.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from apply_ablate.record import AblationRecord


class ApplyError(RuntimeError):
    """Raised when a destination cannot be prepared or a target cannot be resolved."""


# Heavy prebuilt-dependency dirs: never copied (can be many GB, e.g. a Lean `.lake`
# holds all of mathlib's `.olean`). They are symlinked back to `src` instead, which is
# safe because single-file checks (`lake env lean`, `coqc`) only *read* them.
_LINK_DIRS = (".lake", "lake-packages", "_build", "build")

# Proof-source extensions we scan when withholding files that use a deleted lemma.
_PROOF_EXTS = {".v", ".thy", ".lean"}


def find_lemma_user_files(
    src: Path, names: list[str], target_rel: Path, *, skip: set[str]
) -> set[Path]:
    """Repo-relative proof files (other than `target_rel`) that mention any of `names`.

    Used to keep the ablation honest: a deleted lemma's *other* in-repo use-sites would
    otherwise be readable by the agent (via `read_file`/`search`) and leak the answer.
    Any such file imports the ablated file, so it is downstream of the challenge's
    compile closure — withholding it never breaks the single-file build.

    Matching is whole-word by name over proof-source files only (`.v/.thy/.lean`); heavy
    dep dirs (`_LINK_DIRS`) and `skip` names are pruned. Caveat: a *different* file that
    coincidentally defines a same-named lemma also matches and is withheld — acceptable,
    since such a file is not needed to build the (downstream-free) target closure.
    """
    names = [n for n in names if n]
    if not names:
        return set()
    pat = re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b")
    pruned = set(skip) | set(_LINK_DIRS)
    found: set[Path] = set()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in pruned]
        for fname in files:
            p = Path(root) / fname
            if p.suffix not in _PROOF_EXTS:
                continue
            rel = p.relative_to(src)
            if rel == target_rel:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pat.search(text):
                found.add(rel)
    return found


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
    exclude: frozenset[Path] = frozenset(),
) -> None:
    """Mirror `src` into `dst` with **symlinks**, materialising only the directories
    along `target_rel` as real dirs — so the caller can write a real file at
    `dst/target_rel` without touching `src`, and every other file/dir is a symlink back
    to `src` (no bulk copy).

    This avoids duplicating large in-place build trees per check: Coq's `.vo` live
    beside the `.v` under `src/`, so a plain copy duplicates the whole repo every
    challenge. Single-file checks (`coqc`, `lake env lean`, `isabelle build`) only read
    the deps, so symlinks resolve the load path while the one edited file stays real.

    `exclude` names repo-relative files to **omit entirely** from the overlay (no
    symlink, so they are invisible to the agent). Their ancestor dirs are materialised
    real precisely so the sibling files around them can still be symlinked individually
    while the excluded file itself is dropped.
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
    # directories that must be real: every ancestor of the target AND of each excluded
    # file (so an excluded file deep in a sibling dir can be skipped individually rather
    # than pulled in wholesale by a directory symlink).
    real_dirs: set[Path] = set()
    for leaf in {target_rel, *exclude}:
        cur = Path()
        for part in leaf.parts[:-1]:
            cur = cur / part
            real_dirs.add(cur)

    def build(rel: Path) -> None:
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for entry in sorted((src / rel).iterdir()):
            er = rel / entry.name
            if er == target_rel or entry.name in skip or er in exclude:
                continue  # target written real by caller; skip ignored/withheld names
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
    withhold_lemma_users: bool = True,
) -> Path:
    """Overlay SRC->DST (symlinks) and write the challenge (or recovered solution) at
    the target path in DST.

    Returns the absolute path of the file that was written. Uses a symlink overlay
    (see `overlay_repo`) so per-challenge setup is O(files-along-path), not a full copy.

    With `withhold_lemma_users` (default), any *other* repo file that uses one of the
    record's deleted lemmas is omitted from the overlay so the agent cannot read the
    answer out of a sibling use-site. Those files are downstream of the target, so this
    never affects the single-file build. No-op when nothing was deleted.
    """
    # resolve the target's path relative to the pristine source, then overlay
    direct = src / record.file_path
    if direct.is_file():
        target_rel = Path(record.file_path)
    else:
        target_rel = resolve_target(src, record.file_path).relative_to(src)
    exclude: frozenset[Path] = frozenset()
    if withhold_lemma_users:
        exclude = frozenset(
            find_lemma_user_files(
                src,
                record.deleted_lemma_names,
                target_rel,
                skip=set() if include_git else {".git"},
            )
        )
        if exclude:
            try:  # best-effort transparency; never let logging break apply
                from apply_ablate.obs import log

                log(
                    "overlay-withheld",
                    target=str(target_rel),
                    deleted_lemmas=record.deleted_lemma_names,
                    withheld_count=len(exclude),
                    withheld_files=sorted(str(p) for p in exclude),
                )
            except Exception:  # noqa: BLE001
                pass
    overlay_repo(
        src,
        dst,
        target_rel,
        overwrite=overwrite,
        include_git=include_git,
        exclude=exclude,
    )
    target = dst / target_rel
    # The overlay symlinks the target's OWN build artifacts (e.g. `Foo.vo`, `Foo.glob`)
    # back to src. Compiling the challenge writes `Foo.vo` next to the source — but if
    # that path is a symlink into src, the write FOLLOWS it and clobbers the pristine
    # `src/Foo.vo`, corrupting the shared dep cache for every other challenge that
    # requires Foo. Drop those companion symlinks so the compiler writes fresh, real
    # artifacts inside the overlay instead. (Only the target is ever compiled, so only
    # its companions matter; dependency .vo stay symlinked and read-only.)
    for ext in (".vo", ".vos", ".vok", ".glob", ".v.beautified", ".required_vo"):
        companion = target.parent / (target.stem + ext)
        if companion.is_symlink() or companion.exists():
            companion.unlink()
    content = record.solution_text() if solution else record.challenge_file_content
    target.parent.mkdir(parents=True, exist_ok=True)
    write_content(target, content)
    return target
