"""Lean backend: `lake build` to prepare, `lake env lean <file>` to check.

Mirrors `lean-ablator/Main.lean`'s `--check-build` (`checkCompiles`). `lake env lean`
compiles only the single file against prebuilt `.olean` deps and never its dependents.
`sorry` always elaborates (with a warning) so a holed challenge compiles cleanly;
`allow_holes` therefore does not change the command — a recovered solution simply has
no `sorry` and so must compile warning-or-not at exit 0.
"""

from __future__ import annotations

from pathlib import Path

from apply_ablate.provers.base import CheckResult, run

_LAKEFILES = ("lakefile.toml", "lakefile.lean")


def _lake_root(start: Path) -> Path:
    """Nearest ancestor (inclusive of `start`'s dir) containing a lakefile."""
    here = start if start.is_dir() else start.parent
    for d in (here, *here.parents):
        if any((d / name).exists() for name in _LAKEFILES):
            return d
    return here


class LeanProver:
    key = "lean"

    def prepare(self, repo: Path, *, timeout: int) -> CheckResult:
        root = _lake_root(repo)
        return run(["lake", "build"], cwd=root, timeout=timeout)

    def check(
        self, repo: Path, rel: Path, *, allow_holes: bool, timeout: int
    ) -> CheckResult:
        target = (repo / rel).resolve()
        root = _lake_root(target)
        return run(
            ["lake", "env", "lean", str(target)],
            cwd=root,
            timeout=timeout,
        )
