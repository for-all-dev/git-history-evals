"""Lean backend: `lake build` to prepare, `lake env lean <file>` to check.

Mirrors `lean-ablator/Main.lean`'s `--check-build` (`checkCompiles`). `lake env lean`
compiles only the single file against prebuilt `.olean` deps and never its dependents.
`sorry` always elaborates (with a warning) so a holed challenge compiles cleanly;
`allow_holes` therefore does not change the command — a recovered solution simply has
no `sorry` and so must compile warning-or-not at exit 0.
"""

from __future__ import annotations

import os
from pathlib import Path

from apply_ablate.provers.base import CheckResult, run

_LAKEFILES = ("lakefile.toml", "lakefile.lean")

# Substring `lake env lean` prints when it refuses to run because the package
# configuration is stale/unbuildable (e.g. a `lakefile.lean` whose default target
# includes a C-FFI test exe that won't link in this environment — lean-zip). The
# library oleans are fine, so we fall back to invoking `lean` directly.
_INVALID_CONFIG = "compiled configuration is invalid"


def _lean_path(root: Path) -> str:
    """Colon-joined olean search path across the package's own build lib and every
    dependency's, approximating what `lake env` would export. Covers both the
    `build/lib` and `build/lib/lean` layouts used across Lean toolchains."""
    parts: list[str] = []
    for base in [root, *sorted((root / ".lake" / "packages").glob("*"))]:
        for sub in ("lib", "lib/lean"):
            d = base / ".lake" / "build" / sub
            if d.is_dir():
                parts.append(str(d))
    return os.pathsep.join(parts)


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
        res = run(["lake", "env", "lean", str(target)], cwd=root, timeout=timeout)
        # Fallback: when lake refuses because the package config is invalid (a broken
        # default target — e.g. an unlinkable FFI test exe), the library oleans still
        # exist, so drive `lean` directly with a reconstructed LEAN_PATH.
        if not res.ok and _INVALID_CONFIG in (res.stderr or res.stdout or ""):
            lp = _lean_path(root)
            if lp:
                env = dict(os.environ)
                env["LEAN_PATH"] = (
                    lp + os.pathsep + env["LEAN_PATH"] if env.get("LEAN_PATH") else lp
                )
                return run(
                    ["lean", str(target)], cwd=root, timeout=timeout, env=env
                )
        return res
